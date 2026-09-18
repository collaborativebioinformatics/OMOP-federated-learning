"""Check one site's OMOP output against the data contract, and optionally against a reference output.

Usage:
    python validate_contract.py output/site_a
    python validate_contract.py output/site_a --reference ../synthea_datasets/omop/site_a
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

CONTRACT_FILE = Path(__file__).resolve().parent.parent / "contract.yaml"


def load_contract(path: Path | None = None) -> dict:
    return yaml.safe_load(Path(path or CONTRACT_FILE).read_text(encoding="utf-8"))


def read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def check(site: Path, contract: dict | None = None, allow_extra: bool = False) -> list[str]:
    """Check one site folder. With allow_extra, the contract's columns must be present in any order,
    and extra columns and tables are ignored, as in route B output."""
    contract = contract or load_contract()
    site = Path(site)
    problems: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    for table, spec in contract["tables"].items():
        path = site / f"{table}.csv"
        if not path.is_file():
            problems.append(f"{table}: file missing")
            continue
        df = read(path)
        frames[table] = df
        expected = list(spec["columns"])
        if allow_extra:
            missing = [c for c in expected if c not in df.columns]
            if missing:
                problems.append(f"{table}: contract columns missing: {missing}")
                continue
        elif list(df.columns) != expected:
            problems.append(f"{table}: columns {list(df.columns)} differ from contract {expected}")
            continue
        id_column = expected[0]
        if df[id_column].duplicated().any():
            problems.append(f"{table}.{id_column}: duplicate IDs")
        for column, rule in spec["columns"].items():
            values = df[column]
            filled = values[values != ""]
            empty = len(values) - len(filled)
            if empty and rule.get("required", True):
                problems.append(f"{table}.{column}: {empty} empty values")
            kind = rule["type"]
            if kind == "integer":
                bad = ~filled.str.fullmatch(r"-?\d+")
            elif kind == "decimal":
                bad = pd.to_numeric(filled, errors="coerce").isna()
            elif kind == "date":
                bad = ~filled.str.fullmatch(r"\d{4}-\d{2}-\d{2}")
            else:
                bad = pd.Series(False, index=filled.index)
            if bad.any():
                example = filled[bad].iloc[0]
                problems.append(f"{table}.{column}: {int(bad.sum())} values are not {kind}, e.g. {example!r}")
            elif "allowed" in rule:
                unknown = sorted(set(filled.astype(int)) - set(rule["allowed"]))
                if unknown:
                    problems.append(f"{table}.{column}: concept IDs not in the contract: {unknown[:10]}")
    if "person" in frames and "person_id" in frames["person"]:
        persons = set(frames["person"]["person_id"])
        for table, df in frames.items():
            if table != "person" and "person_id" in df:
                orphans = set(df["person_id"]) - persons
                if orphans:
                    problems.append(f"{table}.person_id: {len(orphans)} IDs missing from person")
    for rule in contract.get("plausible_ranges", []):
        df = frames.get(rule["table"])
        concept_column = rule.get("concept_column", "measurement_concept_id")
        value_column = rule.get("value_column", "value_as_number")
        if df is None or concept_column not in df or value_column not in df:
            continue
        rows = df[df[concept_column] == str(rule["concept_id"])]
        values = pd.to_numeric(rows[value_column], errors="coerce").dropna()
        outside = values[(values < rule["min"]) | (values > rule["max"])]
        if len(outside):
            problems.append(
                f"{rule['table']} concept {rule['concept_id']}: {len(outside)} values outside "
                f"{rule['min']}-{rule['max']}, e.g. {outside.iloc[0]}"
            )
    return problems


def compare(site: Path, reference: Path, contract: dict | None = None) -> list[str]:
    contract = contract or load_contract()
    site, reference = Path(site), Path(reference)
    differences: list[str] = []
    for table, spec in contract["tables"].items():
        ours_path, reference_path = site / f"{table}.csv", reference / f"{table}.csv"
        if not ours_path.is_file() or not reference_path.is_file():
            differences.append(f"{table}: file missing in output or reference")
            continue
        ours, theirs = read(ours_path), read(reference_path)
        if len(ours) != len(theirs):
            differences.append(f"{table}: {len(ours)} rows, reference has {len(theirs)}")
            continue
        for column, rule in spec["columns"].items():
            if column not in ours or column not in theirs:
                differences.append(f"{table}.{column}: column missing in output or reference")
                continue
            a, b = ours[column], theirs[column]
            if rule["type"] == "decimal":
                x, y = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
                same = ((x - y).abs() < 1e-9) | (x.isna() & y.isna())
            else:
                same = a == b
            if not same.all():
                row = int((~same).to_numpy().argmax())
                differences.append(
                    f"{table}.{column}: {int((~same).sum())} rows differ, "
                    f"first at row {row + 1}: {a.iloc[row]!r} vs reference {b.iloc[row]!r}"
                )
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site", type=Path, help="output folder of one site")
    parser.add_argument("--reference", type=Path, help="trusted output for the same raw data")
    parser.add_argument("--contract", type=Path, help="contract YAML, default omop_skill/contract.yaml")
    parser.add_argument("--allow-extra", action="store_true", help="accept extra OMOP columns and tables (route B output)")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    problems = check(args.site, contract, allow_extra=args.allow_extra)
    print(f"Contract check for {args.site}:")
    print("  OK" if not problems else "\n".join(f"  - {p}" for p in problems))
    differences: list[str] = []
    if args.reference:
        differences = compare(args.site, args.reference, contract)
        print(f"Comparison with {args.reference}:")
        print("  identical" if not differences else "\n".join(f"  - {d}" for d in differences))
    return 1 if problems or differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
