"""Split one converted run into site folders by person_id (data contract, section 4).

Every person goes to the site at index person_id % n of --sites, so with the default
site_a gets remainder 0, site_b remainder 1, site_c remainder 2. Rows of the other tables
follow their person.

Usage:
    python split_sites.py omop/all omop --sites site_a site_b site_c
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

TABLES = ("person", "observation_period", "measurement", "condition_occurrence")


def sql_path(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def split(source: Path, target: Path, sites: list[str]) -> dict[str, int]:
    con = duckdb.connect()
    persons: dict[str, int] = {}
    try:
        for index, site in enumerate(sites):
            out = target / site
            out.mkdir(parents=True, exist_ok=True)
            for table in TABLES:
                # all_varchar keeps every value exactly as the ETL wrote it
                relation = f"read_csv({sql_path(source / (table + '.csv'))}, header = true, all_varchar = true)"
                con.execute(
                    f"COPY (SELECT * FROM {relation} WHERE CAST(person_id AS BIGINT) % {len(sites)} = {index} "
                    f"ORDER BY CAST({'person_id' if table == 'person' else table + '_id'} AS BIGINT)) "
                    f"TO {sql_path(out / (table + '.csv'))} (HEADER, DELIMITER ',', QUOTE '\"')"
                )
            persons[site] = con.execute(
                f"SELECT count(*) FROM read_csv({sql_path(out / 'person.csv')}, header = true)"
            ).fetchone()[0]
    finally:
        con.close()
    return persons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="folder with the converted run, e.g. omop/all")
    parser.add_argument("target", type=Path, help="parent folder for the site folders, e.g. omop")
    parser.add_argument("--sites", nargs="+", default=["site_a", "site_b", "site_c"])
    args = parser.parse_args()
    for site, count in split(args.source, args.target, args.sites).items():
        print(f"{site}: {count} persons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
