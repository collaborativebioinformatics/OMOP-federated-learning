"""The cohort, the landmark and the label shared by every site of the UKB pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa

import omopflare as of

HERE = Path(__file__).parent
T2DM = 201826

CANDIDATES = (
    of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),
    of.Feature("sbp", 3004249, "measurement", unit_concept_id=8876, plausible_range=(50.0, 250.0)),
)
VOCABULARY_VERSION = "ukb-synthetic-contract"
LOOKBACK_DAYS = 365
TEST_FRACTION = 0.25


def site_paths(root: Path) -> tuple[Path, ...]:
    """List the site directories under an OMOP folder.

    Args:
        root: Directory holding one folder per site.

    Returns:
        The site directories, sorted by name.

    Raises:
        FileNotFoundError: If the directory holds no sites.
    """
    paths = tuple(sorted(path for path in root.iterdir() if path.is_dir()))
    if not paths:
        raise FileNotFoundError(f"no site directories under {root}")
    return paths


def index_table(source: of.OmopSource) -> pa.Table:
    """Build the landmark and the incident-diabetes label for one site.

    The landmark is the day after the first assessment, so the assessment's own measurements fall
    inside the lookback window and none of them is read on or after the landmark.
    People already diagnosed before the landmark are prevalent cases and leave the cohort.

    Args:
        source: The site to read from.

    Returns:
        A table of ``person_id``, ``index_date`` and ``label``.
    """
    query = f"""
    with landmark as (
        select person_id, min(measurement_date) + interval '1 day' as index_date
        from measurement
        group by person_id
    )
    select
        l.person_id,
        cast(l.index_date as date) as index_date,
        cast(count(c.person_id) filter (where c.condition_start_date >= l.index_date) > 0 as double) as label
    from landmark l
    left join condition_occurrence c
        on c.person_id = l.person_id
       and c.condition_concept_id = {T2DM}
    group by l.person_id, l.index_date
    having count(c.person_id) filter (where c.condition_start_date < l.index_date) = 0
    """
    return source.sql(query).arrow().read_all()


def split(person_ids: np.ndarray, *, test_fraction: float = TEST_FRACTION) -> np.ndarray:
    """Assign people to the training or the test half, the same way at every site and every run.

    Args:
        person_ids: The people to split.
        test_fraction: Share of people held out.

    Returns:
        A boolean mask that is True for the test people.
    """
    mixed = (np.asarray(person_ids, dtype=np.uint64) * np.uint64(2654435761)) % np.uint64(1000)
    return mixed < np.uint64(round(test_fraction * 1000))
