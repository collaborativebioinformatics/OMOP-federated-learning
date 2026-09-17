"""Landmarked feature extraction, evaluated in duckdb and streamed out in person blocks."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pyarrow as pa

from .source import OmopSource
from .spec import CONCEPT_COLUMN, DATE_COLUMN, VALUE_COLUMN, Feature, FeatureSpec

INDEX_TABLE = "omopflare_index"


def _numeric_case(feature: Feature, alias: str) -> str:
    value = VALUE_COLUMN[feature.domain]
    guards = [f"e.{CONCEPT_COLUMN[feature.domain]} = {feature.concept_id}"]
    if feature.unit_concept_id is not None:
        guards.append(f"e.unit_concept_id = {feature.unit_concept_id}")
    if feature.plausible_range is not None:
        low, high = feature.plausible_range
        guards.append(f"e.{value} between {low} and {high}")
    return f"case when {' and '.join(guards)} then e.{value} end as {alias}"


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


def extract(
    source: OmopSource,
    spec: FeatureSpec,
    index: pa.Table,
    *,
    batch_size: int = 50_000,
) -> Iterator[pa.RecordBatch]:
    """Yield design-matrix batches for the people in ``index``.

    ``index`` needs a ``person_id`` and an ``index_date`` column, the landmark each patient's window ends at.
    Only events strictly before the landmark and within ``lookback_days`` are read, so nothing after the prediction
    time can leak into a feature.
    Rows are also clipped to the patient's observation period, because absence outside that period is not evidence
    that an event did not happen.
    """
    required = {"person_id", "index_date"}
    missing = required - set(index.column_names)
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


def to_matrix(batch: pa.RecordBatch, spec: FeatureSpec) -> tuple[np.ndarray, np.ndarray]:
    """Turn one extraction batch into person IDs and a dense float matrix.

    Missing values stay as NaN so imputation is the caller's explicit decision rather than a silent zero.
    """
    person_ids = np.asarray(batch.column("person_id"))
    columns = [np.asarray(batch.column(f"f{position}"), dtype=np.float64) for position in range(len(spec.features))]
    matrix = np.column_stack(columns) if columns else np.empty((len(person_ids), 0))
    if spec.missing_indicators:
        numeric = [position for position, f in enumerate(spec.features) if f.is_numeric]
        indicators = np.isnan(matrix[:, numeric]).astype(np.float64)
        matrix = np.hstack([matrix, indicators])
    return person_ids, matrix
