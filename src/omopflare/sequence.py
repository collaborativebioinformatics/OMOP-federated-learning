from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Literal

import numpy as np
import pyarrow as pa
import sparse

from .features import Index, as_table
from .source import OmopSource
from .spec import CONCEPT_COLUMN, DATE_COLUMN, VALUE_COLUMN, FeatureSpec

if TYPE_CHECKING:
    from ehrdata import EHRData

FEATURE_TABLE = "omopflare_features"
BLOCK_TABLE = "omopflare_block"
Aggregate = Literal["last", "mean", "max", "count"]


def _feature_table(spec: FeatureSpec) -> pa.Table:
    return pa.table(
        {
            "position": pa.array(range(len(spec.features)), pa.int32()),
            "concept_id": pa.array([f.concept_id for f in spec.features], pa.int64()),
            "domain": pa.array([f.domain for f in spec.features]),
            "unit_concept_id": pa.array([f.unit_concept_id for f in spec.features], pa.int64()),
            "low": pa.array([f.plausible_range[0] if f.plausible_range else None for f in spec.features], pa.float64()),
            "high": pa.array(
                [f.plausible_range[1] if f.plausible_range else None for f in spec.features], pa.float64()
            ),
        }
    )


def _domain_query(domain: str, spec: FeatureSpec, bins: int, aggregate: Aggregate) -> str:
    date = DATE_COLUMN[domain]  # type: ignore[index]
    value_column = VALUE_COLUMN[domain]  # type: ignore[index]
    value = f"try_cast(e.{value_column} as double)" if value_column else "1.0"
    unit_guard = "and (f.unit_concept_id is null or e.unit_concept_id = f.unit_concept_id)" if value_column else ""
    range_guard = f"and (f.low is null or {value} between f.low and f.high)" if value_column else ""
    reducer = {
        "last": f"max_by({value}, e.{date})",
        "mean": f"avg({value})",
        "max": f"max({value})",
        "count": "count(*)::double",
    }[aggregate]
    return f"""
    select
        b.row_index,
        f.position,
        least({bins - 1}, (({spec.lookback_days} - date_diff('day', e.{date}, i.index_date)) * {bins})
              / {spec.lookback_days}) as bin,
        {reducer} as value
    from {BLOCK_TABLE} b
    join {BLOCK_TABLE} i on i.row_index = b.row_index
    join {domain} e on e.person_id = b.person_id
    join {FEATURE_TABLE} f on f.concept_id = e.{CONCEPT_COLUMN[domain]} and f.domain = '{domain}'
    where e.{date} < i.index_date
      and e.{date} >= i.index_date - interval '{spec.lookback_days}' day
      {unit_guard}
      {range_guard}
    group by 1, 2, 3
    """


def extract_sequence(
    source: OmopSource,
    spec: FeatureSpec,
    index: Index,
    *,
    bins: int,
    aggregate: Aggregate = "last",
    block_size: int = 50_000,
) -> Iterator[tuple[np.ndarray, sparse.COO]]:
    """Yield sparse patient by feature by time-bin tensors.

    Bin 0 is oldest and bin ``bins - 1`` ends at the landmark. Unstored entries read as NaN.

    Args:
        source: The site to read from.
        spec: The frozen feature schema.
        index: SQL, a duckdb relation or an Arrow table with ``person_id`` and ``index_date``.
        bins: Number of time bins across ``spec.lookback_days``.
        aggregate: How to reduce several events in one bin.
        block_size: People per yielded tensor.

    Yields:
        The person IDs of the block, and a ``sparse.COO`` of shape ``(people, features, bins)``.

    Raises:
        ValueError: If ``bins`` is not positive or ``index`` lacks the required columns.
    """
    if bins <= 0:
        raise ValueError(f"bins must be positive, got {bins}")
    index = as_table(source, index)
    missing = {"person_id", "index_date"} - set(index.column_names)
    if missing:
        raise ValueError(f"index table is missing {sorted(missing)}")

    source.connection.register(FEATURE_TABLE, _feature_table(spec))
    domains = [d for d in spec.domains if source.has(d)]
    if not domains:
        raise ValueError("none of the spec's domains are present at this site")

    person_ids = np.asarray(index.column("person_id"))
    for start in range(0, len(person_ids), block_size):
        block = index.slice(start, block_size)
        rows = pa.table(
            {
                "row_index": pa.array(range(block.num_rows), pa.int32()),
                "person_id": block.column("person_id"),
                "index_date": block.column("index_date"),
            }
        )
        source.connection.register(BLOCK_TABLE, rows)
        query = " union all ".join(_domain_query(d, spec, bins, aggregate) for d in domains)
        result = source.connection.execute(query).arrow().read_all()
        coords = np.vstack(
            [
                np.asarray(result.column("row_index"), dtype=np.int64),
                np.asarray(result.column("position"), dtype=np.int64),
                np.asarray(result.column("bin"), dtype=np.int64),
            ]
        )
        values = np.asarray(result.column("value"), dtype=np.float64)
        tensor = sparse.COO(
            coords=coords,
            data=values,
            shape=(block.num_rows, len(spec.features), bins),
            fill_value=np.nan,
        )
        yield np.asarray(block.column("person_id")), tensor


def to_ehrdata(person_ids: np.ndarray, tensor: sparse.COO, spec: FeatureSpec) -> EHRData:
    """Wrap a sequence tensor as an :class:`~ehrdata.EHRData`.

    Args:
        person_ids: One ID per row of ``tensor``.
        tensor: A tensor from :func:`extract_sequence`.
        spec: The spec the tensor was extracted with.

    Returns:
        An ``EHRData`` whose ``X`` is the sparse tensor, kept sparse rather than densified.

    Raises:
        ImportError: If ehrdata is not installed.
    """
    try:
        import pandas as pd
        from ehrdata import EHRData
    except ImportError as error:  # pragma: no cover
        raise ImportError("to_ehrdata needs ehrdata; pip install ehrdata") from error

    obs = pd.DataFrame(index=pd.Index([str(p) for p in person_ids], name="person_id"))
    var = pd.DataFrame(
        {
            "concept_id": [f.concept_id for f in spec.features],
            "domain": [f.domain for f in spec.features],
            "unit_concept_id": [f.unit_concept_id for f in spec.features],
        },
        index=pd.Index([f.name for f in spec.features], name="feature"),
    )
    bins = tensor.shape[2]
    width = spec.lookback_days / bins
    tem = pd.DataFrame(
        {"days_before_index": [round(spec.lookback_days - (i + 1) * width, 3) for i in range(bins)]},
        index=pd.Index([str(i) for i in range(bins)], name="bin"),
    )
    return EHRData(X=tensor, obs=obs, var=var, tem=tem)
