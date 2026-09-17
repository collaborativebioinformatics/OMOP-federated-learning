"""Profile the raw CSV files of one source folder with DuckDB.

Writes a Markdown report with row counts, a summary per column, and the most frequent codes,
so a mapping can be written from facts about the data instead of guesses.

Usage:
    python profile_source.py --source raw/site_a/csv --out work/profile_site_a.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

CODE_COLUMNS = {"code", "gender", "units", "race", "ethnicity", "category", "encounterclass", "system", "type"}
MAX_DISTINCT = 5000
TOP = 25


def cell(value: object) -> str:
    missing = value is None or (isinstance(value, float) and value != value)
    text = "" if missing else str(value)
    return text.replace("|", "\\|").replace("\n", " ")[:40]


def profile(source: Path, files: list[str] | None = None) -> str:
    source = Path(source)
    paths = [source / name for name in files] if files else sorted(source.glob("*.csv"))
    con = duckdb.connect()
    lines = [f"# Source profile: `{source.as_posix()}`", ""]
    for path in paths:
        relation = f"read_csv('{path.resolve().as_posix()}', header = true, all_varchar = true)"
        rows = con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
        lines += [f"## {path.name}: {rows} rows", ""]
        summary = con.execute(f"SUMMARIZE SELECT * FROM {relation}").df()
        columns = list(summary["column_name"])
        lines += ["| column | distinct (approx.) | null % | min | max |", "|---|---|---|---|---|"]
        for _, row in summary.iterrows():
            lines.append(
                f"| {row['column_name']} | {row['approx_unique']} | {row['null_percentage']} "
                f"| {cell(row['min'])} | {cell(row['max'])} |"
            )
        lines.append("")
        description = next((c for c in columns if c.lower() == "description"), None)
        for _, row in summary.iterrows():
            column = row["column_name"]
            if column.lower() not in CODE_COLUMNS or row["approx_unique"] > MAX_DISTINCT:
                continue
            if column.lower() == "code" and description:
                query = (
                    f'SELECT "{column}", any_value("{description}"), count(*) AS n FROM {relation} '
                    f"GROUP BY 1 ORDER BY n DESC LIMIT {TOP}"
                )
                header = ["| value | description | rows |", "|---|---|---|"]
            else:
                query = f'SELECT "{column}", count(*) AS n FROM {relation} GROUP BY 1 ORDER BY n DESC LIMIT {TOP}'
                header = ["| value | rows |", "|---|---|"]
            lines += [f"Top {TOP} values of `{column}`:", ""] + header
            for values in con.execute(query).fetchall():
                lines.append("| " + " | ".join(cell(v) for v in values) + " |")
            lines.append("")
    con.close()
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="folder with the raw CSV files")
    parser.add_argument("--out", type=Path, required=True, help="Markdown report to write")
    parser.add_argument("--files", nargs="*", help="only these files, e.g. patients.csv conditions.csv")
    args = parser.parse_args()
    report = profile(args.source, args.files)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
