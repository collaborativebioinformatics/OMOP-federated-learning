"""Load Synthea CSV files and Athena vocabulary files into the tables ETL-Synthea created in a DuckDB file.

Replaces ETLSyntheaBuilder::LoadSyntheaTables and ::LoadVocabFromCsv, which leave the tables empty on DuckDB.
Columns are matched by name, values are cast to the type of the target column.

Usage:
    python load_csv_duckdb.py <etl_synthea.duckdb> --synthea <csv_dir> [--vocabulary <athena_dir>]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb


def sql_path(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def cast(source: str, data_type: str) -> str:
    column = '"' + source.replace('"', '""') + '"'
    kind = data_type.upper()
    if kind == "DATE":
        # Athena writes 20240101, Synthea writes 2024-01-01 or 2024-01-01T08:00:00Z
        return (
            f"CASE WHEN regexp_full_match({column}, '[0-9]{{8}}') THEN CAST(strptime({column}, '%Y%m%d') AS DATE) "
            f"ELSE TRY_CAST(substr({column}, 1, 10) AS DATE) END"
        )
    if kind.startswith("TIMESTAMP"):
        return f"TRY_CAST(replace(replace({column}, 'T', ' '), 'Z', '') AS TIMESTAMP)"
    if kind.startswith("VARCHAR") or kind == "TEXT":
        return column
    return f"TRY_CAST({column} AS {kind})"


def load_file(con: duckdb.DuckDBPyConnection, schema: str, path: Path, vocabulary: bool) -> None:
    table = path.stem.lower()
    targets = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
        [schema, table],
    ).fetchall()
    if not targets:
        print(f"  skip {path.name}: no table {schema}.{table}")
        return
    options = "header = true, all_varchar = true" + (", delim = '\\t', quote = '', escape = ''" if vocabulary else "")
    relation = f"read_csv({sql_path(path)}, {options})"
    sources = {name.lower(): name for (name, *_rest) in con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()}
    select, missing = [], []
    for name, data_type in targets:
        source = sources.get(name.lower())
        if source is None:
            missing.append(name)
            select.append(f'NULL AS "{name}"')
        else:
            select.append(f'{cast(source, data_type)} AS "{name}"')
    started = time.time()
    con.execute(f'DELETE FROM {schema}."{table}"')
    con.execute(f'INSERT INTO {schema}."{table}" SELECT {", ".join(select)} FROM {relation}')
    rows = con.execute(f'SELECT count(*) FROM {schema}."{table}"').fetchone()[0]
    note = f", no source column for {missing}" if missing else ""
    print(f"  {schema}.{table:<22} {rows:>10} rows  {time.time() - started:5.1f} s{note}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path)
    parser.add_argument("--synthea", type=Path, required=True, help="folder with the Synthea CSV export")
    parser.add_argument("--vocabulary", type=Path, help="folder with the unzipped Athena download")
    args = parser.parse_args()
    con = duckdb.connect(str(args.database))
    try:
        print("Synthea tables:")
        for path in sorted(args.synthea.glob("*.csv")):
            load_file(con, "native", path, vocabulary=False)
        if args.vocabulary:
            print("Vocabulary tables:")
            for path in sorted(args.vocabulary.glob("*.csv")):
                load_file(con, "cdm", path, vocabulary=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
