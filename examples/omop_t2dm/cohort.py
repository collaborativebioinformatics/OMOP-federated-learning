from __future__ import annotations

from pathlib import Path

import pyarrow as pa

import omopflare as of

HERE = Path(__file__).parent
COHORTS = HERE.parents[1] / "synthea_cohorts"

T2DM = 201826

CANDIDATES = (
    of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),
    of.Feature("sbp", 3004249, "measurement", unit_concept_id=8876, plausible_range=(50.0, 250.0)),
    of.Feature("glucose", 3000483, "measurement", unit_concept_id=8840, plausible_range=(20.0, 800.0)),
    of.Feature("hba1c", 3004410, "measurement", unit_concept_id=8554, plausible_range=(2.0, 20.0)),
)
VOCABULARY_VERSION = "synthea-contract"
LOOKBACK_DAYS = 3650


def site_paths(root: Path) -> tuple[Path, ...]:
    """List the site directories under a cohort's OMOP folder.

    Args:
        root: Directory holding one folder per site.

    Returns:
        The site directories, sorted by name.

    Raises:
        FileNotFoundError: If the directory holds no sites.
    """
    paths = tuple(sorted(p for p in root.iterdir() if p.is_dir()))
    if not paths:
        raise FileNotFoundError(f"no site directories under {root}")
    return paths


def index_table(source: of.OmopSource) -> pa.Table:
    """Build the landmark and label for one site.

    Args:
        source: The site to read from.

    Returns:
        A table of ``person_id``, ``index_date`` and ``label``.
    """
    landmark = "cast(concat(cast(p.year_of_birth + 50 as varchar), '-01-01') as date)"
    query = f"""
    select
        p.person_id,
        {landmark} as index_date,
        cast(count(c.person_id) > 0 as double) as label
    from person p
    left join condition_occurrence c
        on c.person_id = p.person_id
       and c.condition_concept_id = {T2DM}
       and c.condition_start_date >= {landmark}
    group by p.person_id, p.year_of_birth
    """
    return source.sql(query).arrow().read_all()
