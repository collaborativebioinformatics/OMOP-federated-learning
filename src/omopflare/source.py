"""Read-only access to one site's OMOP tables, without loading them into Python."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import duckdb

CDM_TABLES = (
    "person",
    "observation_period",
    "visit_occurrence",
    "condition_occurrence",
    "drug_exposure",
    "measurement",
    "observation",
    "death",
    "concept",
    "vocabulary",
)
SUFFIXES = (".parquet", ".csv", ".csv.gz")


class OmopSource:
    """A site's OMOP CDM tables, exposed to duckdb as views.

    Tables stay on disk and are scanned with predicate push-down, so a MEASUREMENT table of billions of rows costs
    memory only for the columns and rows a query actually keeps.
    Parquet sorted by ``person_id`` is the fastest layout because a patient's rows are then contiguous.
    """

    def __init__(self, path: str | Path, *, connection: duckdb.DuckDBPyConnection | None = None) -> None:
        self.path = Path(path)
        if not self.path.is_dir():
            raise NotADirectoryError(f"{self.path} is not a directory")
        self.connection = connection or duckdb.connect()
        self._tables = self._register()

    def _register(self) -> Mapping[str, Path]:
        found: dict[str, Path] = {}
        for table in CDM_TABLES:
            for suffix in SUFFIXES:
                candidate = self.path / f"{table}{suffix}"
                if candidate.exists():
                    reader = "read_parquet" if suffix == ".parquet" else "read_csv"
                    self.connection.execute(f"create or replace view {table} as select * from {reader}('{candidate}')")
                    found[table] = candidate
                    break
        if "person" not in found:
            raise FileNotFoundError(f"no person table under {self.path}")
        return found

    @property
    def tables(self) -> Mapping[str, Path]:
        return self._tables

    def has(self, table: str) -> bool:
        return table in self._tables

    def count(self, table: str) -> int:
        if not self.has(table):
            return 0
        return int(self.connection.execute(f"select count(*) from {table}").fetchone()[0])

    def columns(self, table: str) -> tuple[str, ...]:
        rows = self.connection.execute(f"select * from {table} limit 0").description or ()
        return tuple(column[0].lower() for column in rows)

    def vocabulary_version(self) -> str | None:
        """Read the vocabulary release recorded in the VOCABULARY table, if the site ships one."""
        if not self.has("vocabulary"):
            return None
        row = self.connection.execute(
            "select vocabulary_version from vocabulary where vocabulary_id = 'None' limit 1"
        ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def sql(self, query: str) -> duckdb.DuckDBPyRelation:
        return self.connection.sql(query)

    def __repr__(self) -> str:
        return f"OmopSource({self.path}, tables={sorted(self._tables)})"
