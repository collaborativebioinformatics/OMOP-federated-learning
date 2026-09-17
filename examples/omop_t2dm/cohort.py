from __future__ import annotations

from pathlib import Path

import pyarrow as pa

import omopflare as of

HERE = Path(__file__).parent
SITES_ROOT = HERE.parents[1] / "synthea_cohorts" / "cohort_2" / "data" / "omop"

BMI, SBP, T2DM = 3038553, 3004249, 201826
KG_M2, MMHG = 9531, 8876

SPEC = of.FeatureSpec(
    features=(
        of.Feature("bmi", BMI, "measurement", unit_concept_id=KG_M2, plausible_range=(10.0, 80.0)),
        of.Feature("sbp", SBP, "measurement", unit_concept_id=MMHG, plausible_range=(50.0, 250.0)),
    ),
    vocabulary_version="synthea-contract",
    lookback_days=3650,
    missing_indicators=True,
    metadata={"outcome": "type 2 diabetes", "landmark": "age 50"},
)


def sites() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in SITES_ROOT.iterdir() if p.is_dir()))


def index_table(source: of.OmopSource) -> pa.Table:
    """Build the landmark and label for one site.

    The landmark is each patient's fiftieth birthday, and the label is a later type 2 diabetes diagnosis.
    Using a fixed age rather than the diagnosis date keeps the landmark independent of the outcome.

    Args:
        source: The site to read from.

    Returns:
        A table of ``person_id``, ``index_date`` and ``label``.
    """
    query = f"""
    select
        p.person_id,
        cast(concat(cast(p.year_of_birth + 50 as varchar), '-01-01') as date) as index_date,
        cast(count(c.person_id) > 0 as double) as label
    from person p
    left join condition_occurrence c
        on c.person_id = p.person_id
       and c.condition_concept_id = {T2DM}
       and c.condition_start_date >= cast(concat(cast(p.year_of_birth + 50 as varchar), '-01-01') as date)
    group by p.person_id, p.year_of_birth
    """
    return source.sql(query).arrow().read_all()
