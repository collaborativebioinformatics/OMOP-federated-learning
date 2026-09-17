from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

import numpy as np
import pyarrow as pa
import sparse

from .source import OmopSource
from .spec import CONCEPT_COLUMN, DATE_COLUMN, VALUE_COLUMN, Feature, FeatureSpec

INDEX_TABLE = "omopflare_index"
Layout = Literal["dense", "sparse", "auto"]


def _numeric_case(feature: Feature, alias: str) -> str:
    value = f"try_cast(e.{VALUE_COLUMN[feature.domain]} as double)"
    guards = [f"e.{CONCEPT_COLUMN[feature.domain]} = {feature.concept_id}"]
    if feature.unit_concept_id is not None:
        guards.append(f"e.unit_concept_id = {feature.unit_concept_id}")
    if feature.plausible_range is not None:
        low, high = feature.plausible_range
        guards.append(f"{value} between {low} and {high}")
    return f"case when {' and '.join(guards)} then {value} end as {alias}"


def _presence_case(feature: Feature, alias: str) -> str:
    return f"case when e.{CONCEPT_COLUMN[feature.domain]} = {feature.concept_id} then 1.0 end as {alias}"


def _domain_query(source: OmopSource, spec: FeatureSpec, domain: str) -> str | None:
    features = spec.by_domain(domain)  # type: ignore[arg-type]
    if not features or not source.has(domain):
        return None

    date = DATE_COLUMN[domain]  # type: ignore[index]
    cases, picks = [], []
    for position, feature in enumerate(spec.features):
        if feature.domain != domain:
            continue
        alias = f"f{position}"
        cases.append(_numeric_case(feature, alias) if feature.is_numeric else _presence_case(feature, alias))
        picks.append(f"max_by({alias}, e_date) filter (where {alias} is not null) as {alias}")

    return f"""
    select person_id, {", ".join(picks)}
    from (
        select i.person_id, e.{date} as e_date, {", ".join(cases)}
        from {INDEX_TABLE} i
        join {domain} e on e.person_id = i.person_id
        where e.{date} < i.index_date
          and e.{date} >= i.index_date - interval '{spec.lookback_days}' day
    )
    group by person_id
    """.strip()


def _lowercase(table: pa.Table) -> pa.Table:
    return table.rename_columns([name.lower() for name in table.column_names])


def extract(
    source: OmopSource,
    spec: FeatureSpec,
    index: pa.Table,
    *,
    batch_size: int = 50_000,
) -> Iterator[pa.RecordBatch]:
    """Yield design-matrix batches for the people in an index table.

    Reads only events strictly before each landmark, within ``lookback_days``, and inside the observation period.

    Args:
        source: The site to read from.
        spec: The frozen feature schema shared across the federation.
        index: Table with a ``person_id`` column and an ``index_date`` column giving each patient's landmark.
        batch_size: Rows per yielded batch.

    Yields:
        One batch per ``batch_size`` people, with a ``person_id`` column and one ``f<position>`` column per feature.

    Raises:
        ValueError: If ``index`` lacks the required columns, or the spec's domains are all absent from the site.
    """
    index = _lowercase(index)
    missing = {"person_id", "index_date"} - set(index.column_names)
    if missing:
        raise ValueError(f"index table is missing {sorted(missing)}")

    source.connection.register(INDEX_TABLE, index)
    if source.has("observation_period"):
        source.connection.execute(
            f"""
            create or replace temporary view {INDEX_TABLE}_clipped as
            select i.person_id, i.index_date
            from {INDEX_TABLE} i
            join observation_period o on o.person_id = i.person_id
            where i.index_date between o.observation_period_start_date and o.observation_period_end_date
            """
        )

    parts = [q for domain in spec.domains if (q := _domain_query(source, spec, domain))]
    aliases = [f"f{position}" for position in range(len(spec.features))]

    if not parts:
        raise ValueError("none of the spec's domains are present at this site")

    joined = f"select person_id from {INDEX_TABLE}"
    selects = ["base.person_id"]
    froms = [f"({joined}) base"]
    for number, part in enumerate(parts):
        froms.append(f"left join ({part}) d{number} on d{number}.person_id = base.person_id")
    for alias in aliases:
        owner = next((f"d{n}" for n, part in enumerate(parts) if f"as {alias}" in part), None)
        selects.append(f"{owner}.{alias}" if owner else f"cast(null as double) as {alias}")

    query = f"select {', '.join(selects)} from {' '.join(froms)}"
    yield from source.connection.execute(query).to_arrow_reader(batch_size)


def to_matrix(
    batch: pa.RecordBatch,
    spec: FeatureSpec,
    *,
    layout: Layout = "dense",
) -> tuple[np.ndarray, np.ndarray | sparse.COO]:
    """Turn one extraction batch into person IDs and a design matrix.

    ``dense`` keeps missing values as NaN. ``sparse`` returns CSR. ``auto`` uses sparse only for presence-only specs.

    Args:
        batch: A batch from :func:`extract`.
        spec: The spec the batch was extracted with.
        layout: One of ``dense``, ``sparse`` or ``auto``.

    Returns:
        The person IDs, and a matrix whose columns follow ``spec.column_names``.

    Raises:
        ValueError: If ``layout`` is unknown, or ``sparse`` is asked for a spec with numeric features.
    """
    if layout not in ("dense", "sparse", "auto"):
        raise ValueError(f"unknown layout {layout!r}")
    presence_only = not any(f.is_numeric for f in spec.features)
    if layout == "auto":
        layout = "sparse" if presence_only and not spec.missing_indicators else "dense"
    if layout == "sparse":
        return _to_sparse(batch, spec, presence_only=presence_only)
    return _to_dense(batch, spec)


def _to_dense(batch: pa.RecordBatch, spec: FeatureSpec) -> tuple[np.ndarray, np.ndarray]:
    person_ids = np.asarray(batch.column("person_id"))
    columns = [np.asarray(batch.column(f"f{position}"), dtype=np.float64) for position in range(len(spec.features))]
    matrix = np.column_stack(columns) if columns else np.empty((len(person_ids), 0))
    if spec.missing_indicators:
        numeric = [position for position, f in enumerate(spec.features) if f.is_numeric]
        indicators = np.isnan(matrix[:, numeric]).astype(np.float64)
        matrix = np.hstack([matrix, indicators])
    return person_ids, matrix


def _to_sparse(batch: pa.RecordBatch, spec: FeatureSpec, *, presence_only: bool) -> tuple[np.ndarray, sparse.COO]:
    if not presence_only:
        numeric = [f.name for f in spec.features if f.is_numeric]
        raise ValueError(f"a sparse layout needs presence features only, got numeric {numeric}")

    person_ids = np.asarray(batch.column("person_id"))
    rows, columns = [], []
    for position in range(len(spec.features)):
        present = np.flatnonzero(np.asarray(batch.column(f"f{position}").is_valid()))
        rows.append(present)
        columns.append(np.full(present.size, position))
    row = np.concatenate(rows) if rows else np.empty(0, dtype=np.int64)
    column = np.concatenate(columns) if columns else np.empty(0, dtype=np.int64)
    data = np.ones(row.size, dtype=np.float64)
    return person_ids, sparse.COO(
        coords=np.vstack([row, column]),
        data=data,
        shape=(len(person_ids), len(spec.features)),
        fill_value=0.0,
    )
