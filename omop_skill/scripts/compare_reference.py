"""Compare our OMOP output with a reference conversion of the same raw data (data contract, section 8).

Both folders hold person.csv, measurement.csv and condition_occurrence.csv. The two ETLs number their keys
differently, so persons are joined on person_source_value. The reference is filtered to the persons in our
output and to the concept IDs of the contract. observation_period, type concepts and _id keys are not compared.

Usage:
    python compare_reference.py <our_dir> <reference_dir> [--report report.md] [--contract contract.yaml]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import yaml

CONTRACT_FILE = Path(__file__).resolve().parent.parent / "contract.yaml"
TABLES = ("person", "measurement", "condition_occurrence")


def sql_path(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def load(con: duckdb.DuckDBPyConnection, prefix: str, folder: Path) -> None:
    for table in TABLES:
        path = folder / f"{table}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"missing {path}")
        con.execute(
            f"CREATE VIEW {prefix}_{table} AS SELECT * FROM read_csv({sql_path(path)}, header = true, all_varchar = true)"
        )


def one(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return dict(zip(names, cursor.fetchone()))


def compare(ours: Path, reference: Path, contract: dict) -> dict[str, dict]:
    columns = contract["tables"]
    measurement_ids = ", ".join(str(c) for c in columns["measurement"]["columns"]["measurement_concept_id"]["allowed"])
    condition_ids = ", ".join(str(c) for c in columns["condition_occurrence"]["columns"]["condition_concept_id"]["allowed"])
    tolerance = contract.get("comparison", {}).get("measurement_value_tolerance", 0.01)

    con = duckdb.connect()
    load(con, "ours", ours)
    load(con, "ref", reference)
    results: dict[str, dict] = {}

    con.execute("""
        CREATE TABLE op AS SELECT person_id, person_source_value AS psv,
            gender_concept_id AS g, year_of_birth AS y, race_concept_id AS r, ethnicity_concept_id AS e FROM ours_person;
        CREATE TABLE rp AS SELECT person_id, person_source_value AS psv,
            gender_concept_id AS g, year_of_birth AS y, race_concept_id AS r, ethnicity_concept_id AS e FROM ref_person;
    """)
    results["person"] = one(con, """
        SELECT
            (SELECT count(*) FROM op) AS ours,
            (SELECT count(*) FROM rp WHERE psv IN (SELECT psv FROM op)) AS reference_matched,
            (SELECT count(*) FROM op WHERE psv NOT IN (SELECT psv FROM rp)) AS only_ours,
            (SELECT count(*) FROM rp WHERE psv NOT IN (SELECT psv FROM op)) AS only_reference,
            count(*) FILTER (WHERE op.g IS DISTINCT FROM rp.g) AS gender_differs,
            count(*) FILTER (WHERE op.y IS DISTINCT FROM rp.y) AS year_of_birth_differs,
            count(*) FILTER (WHERE op.r IS DISTINCT FROM rp.r) AS race_differs,
            count(*) FILTER (WHERE op.e IS DISTINCT FROM rp.e) AS ethnicity_differs
        FROM op JOIN rp USING (psv)
    """)

    con.execute(f"""
        CREATE TABLE om AS SELECT p.psv, CAST(m.measurement_concept_id AS BIGINT) AS c,
            CAST(substr(m.measurement_date, 1, 10) AS DATE) AS d, TRY_CAST(m.value_as_number AS DOUBLE) AS v
        FROM ours_measurement m JOIN op p ON p.person_id = m.person_id;
        CREATE TABLE rm AS SELECT p.psv, CAST(m.measurement_concept_id AS BIGINT) AS c,
            CAST(substr(m.measurement_date, 1, 10) AS DATE) AS d, TRY_CAST(m.value_as_number AS DOUBLE) AS v
        FROM ref_measurement m JOIN rp p ON p.person_id = m.person_id
        WHERE p.psv IN (SELECT psv FROM op) AND CAST(m.measurement_concept_id AS BIGINT) IN ({measurement_ids});
    """)
    results["measurement"] = one(con, f"""
        WITH counts AS (
            SELECT coalesce(o.psv, r.psv) AS psv, coalesce(o.c, r.c) AS c, o.n AS ours_n, r.n AS ref_n
            FROM (SELECT psv, c, count(*) AS n FROM om GROUP BY ALL) o
            FULL JOIN (SELECT psv, c, count(*) AS n FROM rm GROUP BY ALL) r ON o.psv = r.psv AND o.c = r.c
        ),
        dates AS (
            SELECT o.n AS ours_n, r.n AS ref_n, o.lo AS ours_lo, r.lo AS ref_lo, o.hi AS ours_hi, r.hi AS ref_hi
            FROM (SELECT psv, c, d, count(*) AS n, min(v) AS lo, max(v) AS hi FROM om GROUP BY ALL) o
            FULL JOIN (SELECT psv, c, d, count(*) AS n, min(v) AS lo, max(v) AS hi FROM rm GROUP BY ALL) r
                ON o.psv = r.psv AND o.c = r.c AND o.d = r.d
        )
        SELECT
            (SELECT count(*) FROM om) AS ours_rows,
            (SELECT count(*) FROM rm) AS reference_rows,
            (SELECT count(*) FROM counts) AS person_concept_pairs,
            (SELECT count(*) FROM counts WHERE ours_n IS DISTINCT FROM ref_n) AS pairs_with_different_row_count,
            (SELECT count(*) FROM dates) AS person_concept_dates,
            (SELECT count(*) FROM dates WHERE ours_n IS NULL) AS dates_only_reference,
            (SELECT count(*) FROM dates WHERE ref_n IS NULL) AS dates_only_ours,
            (SELECT count(*) FROM dates WHERE ours_n IS NOT NULL AND ref_n IS NOT NULL
                AND (ours_n <> ref_n OR abs(ours_lo - ref_lo) > {tolerance} OR abs(ours_hi - ref_hi) > {tolerance}
                     OR (ours_lo IS NULL) <> (ref_lo IS NULL))) AS dates_with_different_values
    """)

    con.execute(f"""
        CREATE TABLE oc AS SELECT DISTINCT p.psv, CAST(substr(c.condition_start_date, 1, 10) AS DATE) AS d
        FROM ours_condition_occurrence c JOIN op p ON p.person_id = c.person_id
        WHERE CAST(c.condition_concept_id AS BIGINT) IN ({condition_ids});
        CREATE TABLE rc AS SELECT DISTINCT p.psv, CAST(substr(c.condition_start_date, 1, 10) AS DATE) AS d
        FROM ref_condition_occurrence c JOIN rp p ON p.person_id = c.person_id
        WHERE p.psv IN (SELECT psv FROM op) AND CAST(c.condition_concept_id AS BIGINT) IN ({condition_ids});
    """)
    results["condition_occurrence"] = one(con, """
        SELECT
            (SELECT count(DISTINCT psv) FROM oc) AS ours_persons,
            (SELECT count(DISTINCT psv) FROM rc) AS reference_persons,
            (SELECT count(*) FROM (SELECT psv FROM oc EXCEPT SELECT psv FROM rc)) AS persons_only_ours,
            (SELECT count(*) FROM (SELECT psv FROM rc EXCEPT SELECT psv FROM oc)) AS persons_only_reference,
            (SELECT count(*) FROM (SELECT * FROM oc EXCEPT SELECT * FROM rc)) AS start_dates_only_ours,
            (SELECT count(*) FROM (SELECT * FROM rc EXCEPT SELECT * FROM oc)) AS start_dates_only_reference
    """)
    con.close()
    return results


DIFFERENCE_KEYS = {
    "person": ("only_ours", "only_reference", "gender_differs", "year_of_birth_differs", "race_differs", "ethnicity_differs"),
    "measurement": ("pairs_with_different_row_count", "dates_only_reference", "dates_only_ours", "dates_with_different_values"),
    "condition_occurrence": ("persons_only_ours", "persons_only_reference", "start_dates_only_ours", "start_dates_only_reference"),
}


def report(results: dict[str, dict], ours: Path, reference: Path) -> str:
    lines = [f"# Comparison: `{ours.as_posix()}` against `{reference.as_posix()}`", ""]
    for table, metrics in results.items():
        differences = sum(int(metrics[key] or 0) for key in DIFFERENCE_KEYS[table])
        lines += [f"## {table}: {'identical' if differences == 0 else f'{differences} differences'}", "", "| metric | value |", "|---|---|"]
        lines += [f"| {name} | {value} |" for name, value in metrics.items()]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ours", type=Path, help="our OMOP output, e.g. omop/all")
    parser.add_argument("reference", type=Path, help="reference output of the same raw data, e.g. reference/all")
    parser.add_argument("--report", type=Path, help="also write the Markdown report here")
    parser.add_argument("--contract", type=Path, default=CONTRACT_FILE)
    args = parser.parse_args()
    contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
    results = compare(args.ours, args.reference, contract)
    text = report(results, args.ours, args.reference)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    total = sum(int(results[t][k] or 0) for t in results for k in DIFFERENCE_KEYS[t])
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
