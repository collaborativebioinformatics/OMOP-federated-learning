"""Run a YAML mapping against the raw CSV files of one site with DuckDB and write the OMOP contract tables.

Usage:
    python run_mapping.py --mapping mappings/synthea_3.3.0.yaml --source raw/site_a/csv --out output/site_a
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import yaml


def sql_path(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def load_sources(con: duckdb.DuckDBPyConnection, source: Path, files: dict[str, str]) -> None:
    # One thread while loading, so src_row follows the row order of the source file.
    con.execute("SET threads = 1")
    for name, filename in files.items():
        path = source / filename
        if not path.is_file() and (source / f"{filename}.gz").is_file():
            path = source / f"{filename}.gz"  # DuckDB reads gzipped CSV directly
        if not path.is_file():
            raise FileNotFoundError(f"missing source file: {path}")
        con.execute(
            f"CREATE TABLE {name} AS SELECT row_number() OVER () AS src_row, * "
            f"FROM read_csv({sql_path(path)}, header = true, all_varchar = true)"
        )
    con.execute("RESET threads")


def load_helpers(con: duckdb.DuckDBPyConnection, concept_maps: dict | None) -> None:
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE TABLE concept_map (map_name VARCHAR, source_value VARCHAR, concept_id INTEGER)")
    rows = [
        (map_name, str(code), int(concept_id))
        for map_name, pairs in (concept_maps or {}).items()
        for code, concept_id in pairs.items()
    ]
    if rows:
        con.executemany("INSERT INTO concept_map VALUES (?, ?, ?)", rows)
    con.execute(
        "CREATE MACRO omop_concept(map_key, code_value) AS "
        "(SELECT concept_id FROM concept_map WHERE map_name = map_key AND source_value = code_value)"
    )
    con.execute(
        "CREATE MACRO omop_codes(map_key) AS TABLE "
        "SELECT source_value FROM concept_map WHERE map_name = map_key"
    )
    con.execute("CREATE MACRO omop_date(value) AS strftime(CAST(value AS TIMESTAMPTZ), '%Y-%m-%d')")


def table_sql(spec: dict) -> str:
    select = ",\n  ".join(f"{expression} AS {column}" for column, expression in spec["columns"].items())
    sql = f"SELECT\n  {select}\nFROM {spec['from']}"
    for key, keyword in (("join", ""), ("where", "WHERE "), ("group_by", "GROUP BY ")):
        if spec.get(key):
            sql += f"\n{keyword}{spec[key]}"
    return sql


def run(mapping_path: Path, source: Path, out: Path) -> dict[str, int]:
    mapping = yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        load_sources(con, Path(source), mapping["files"])
        load_helpers(con, mapping.get("concept_maps"))
        counts: dict[str, int] = {}
        for table, spec in mapping["tables"].items():
            sql = table_sql(spec)
            try:
                con.execute(f"CREATE TABLE omop_{table} AS {sql}")
            except duckdb.Error as exc:
                raise RuntimeError(f"table {table} failed:\n{sql}\n{exc}") from exc
            id_column = next(iter(spec["columns"]))
            target = sql_path(out / f"{table}.csv")
            con.execute(f"COPY (SELECT * FROM omop_{table} ORDER BY {id_column}) TO {target} (HEADER, DELIMITER ',')")
            counts[table] = con.execute(f"SELECT count(*) FROM omop_{table}").fetchone()[0]
        return counts
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mapping", type=Path, required=True, help="mapping YAML file")
    parser.add_argument("--source", type=Path, required=True, help="folder with the raw CSV files of one site")
    parser.add_argument("--out", type=Path, required=True, help="output folder for this site")
    args = parser.parse_args()
    for table, rows in run(args.mapping, args.source, args.out).items():
        print(f"{table:<22} {rows:>9} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
