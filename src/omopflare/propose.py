from __future__ import annotations

from collections.abc import Mapping, Sequence

from .source import OmopSource
from .spec import CONCEPT_COLUMN, VALUE_COLUMN, Feature, FeatureSpec
from .stats import MIN_CELL_COUNT


def concept_counts(
    source: OmopSource,
    candidates: Sequence[Feature],
    *,
    min_cell_count: int = MIN_CELL_COUNT,
) -> Mapping[str, int]:
    """Count the patients a site could contribute for each candidate feature.

    Args:
        source: The site to count in.
        candidates: Features the study might use.
        min_cell_count: Counts below this are reported as 0.

    Returns:
        Patients per feature name, suppressed below ``min_cell_count``.
    """
    counts: dict[str, int] = {}
    for feature in candidates:
        if not source.has(feature.domain):
            counts[feature.name] = 0
            continue
        guards = [f"{CONCEPT_COLUMN[feature.domain]} = {feature.concept_id}"]
        value = VALUE_COLUMN[feature.domain]
        if value:
            guards.append(f"{value} is not null")
            if feature.unit_concept_id is not None:
                guards.append(f"unit_concept_id = {feature.unit_concept_id}")
            if feature.plausible_range is not None:
                low, high = feature.plausible_range
                guards.append(f"{value} between {low} and {high}")
        query = f"select count(distinct person_id) from {feature.domain} where {' and '.join(guards)}"
        found = int(source.connection.execute(query).fetchone()[0])
        counts[feature.name] = found if found >= min_cell_count else 0
    return counts


def propose_spec(
    counts: Sequence[Mapping[str, int]],
    candidates: Sequence[Feature],
    *,
    vocabulary_version: str,
    lookback_days: int,
    min_sites: int | None = None,
    min_patients: int = 1,
    missing_indicators: bool = False,
) -> FeatureSpec:
    """Agree one spec from per-site counts, keeping features enough sites can supply.

    Args:
        counts: One mapping per site, from :func:`concept_counts`.
        candidates: The features those counts describe.
        vocabulary_version: Release every site must be on.
        lookback_days: Window before each landmark.
        min_sites: Sites a feature must reach to be kept; defaults to all of them.
        min_patients: Patients a site must have for that site to count as supplying the feature.
        missing_indicators: Append a ``<name>_missing`` column per numeric feature.

    Returns:
        A spec holding only the features that cleared the thresholds.

    Raises:
        ValueError: If no candidate clears them.
    """
    required = len(counts) if min_sites is None else min_sites
    kept = [
        feature
        for feature in candidates
        if sum(1 for site in counts if site.get(feature.name, 0) >= min_patients) >= required
    ]
    if not kept:
        raise ValueError(f"no candidate reached {required} sites with at least {min_patients} patients")
    return FeatureSpec(
        features=tuple(kept),
        vocabulary_version=vocabulary_version,
        lookback_days=lookback_days,
        missing_indicators=missing_indicators,
    )
