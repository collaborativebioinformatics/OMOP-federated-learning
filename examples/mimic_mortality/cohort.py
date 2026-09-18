from __future__ import annotations

import urllib.request
from pathlib import Path

import omopflare as of

HERE = Path(__file__).parent
CDM = HERE / "data"
BASE = "https://physionet.org/files/mimic-iv-demo-omop/0.9/1_omop_data_csv"
TABLES = {
    "person": "person.csv",
    "observation_period": "observation_period.csv",
    "visit_occurrence": "visit_occurrence.csv",
    "measurement": "measurement.csv",
    "condition_occurrence": "condition_occurrence.csv",
    "drug_exposure": "drug_exposure.csv",
    "death": "death.csv",
    "concept": "2b_concept.csv",
    "vocabulary": "2b_vocabulary.csv",
}

VOCABULARY_VERSION = "mimic-iv-demo-omop-0.9"
LOOKBACK_DAYS = 3
SITES = 3

CANDIDATES = (
    of.Feature("resp_rate", 3024171, "measurement", unit_concept_id=8541, plausible_range=(4.0, 60.0)),
    of.Feature("heart_rate", 3027018, "measurement", unit_concept_id=8483, plausible_range=(20.0, 220.0)),
    of.Feature("spo2", 40762499, "measurement", unit_concept_id=8554, plausible_range=(50.0, 100.0)),
    of.Feature("diastolic", 21492240, "measurement", unit_concept_id=8876, plausible_range=(20.0, 150.0)),
    of.Feature("systolic", 21492239, "measurement", unit_concept_id=8876, plausible_range=(40.0, 250.0)),
    of.Feature("potassium", 3023103, "measurement", unit_concept_id=9557, plausible_range=(1.5, 9.0)),
)


def fetch() -> Path:
    """Download the MIMIC-IV demo in OMOP CDM, which PhysioNet publishes as open access.

    Returns:
        The directory holding the CDM tables.
    """
    CDM.mkdir(parents=True, exist_ok=True)
    for table, remote in TABLES.items():
        target = CDM / f"{table}.csv"
        if not target.exists() or target.stat().st_size == 0:
            urllib.request.urlretrieve(f"{BASE}/{remote}", target)
    return CDM


def index_sql(site: int | None = None) -> str:
    """Landmark each patient a day into their first visit and label in-hospital death.

    Args:
        site: Keep only the patients assigned to this site, or all of them when None.

    Returns:
        SQL producing ``person_id``, ``index_date``, ``label`` and ``site``.
    """
    where = "" if site is None else f"where abs(hash(v.person_id)) % {SITES} = {site}"
    return f"""
    select
        v.person_id,
        min(v.visit_start_date) + interval '1' day as index_date,
        cast(max(case when d.person_id is not null then 1 else 0 end) as double) as label
    from visit_occurrence v
    left join death d on d.person_id = v.person_id
    {where}
    group by v.person_id
    """
