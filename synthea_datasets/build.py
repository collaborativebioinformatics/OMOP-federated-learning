"""Generate one Synthea population per site and map each to the OMOP tables of the data contract.

Each site is a different US state and seed, so the cohorts differ in demographics rather than only in sampling.
That is what makes the federation non-IID, and every site becomes one NVFlare client.

Output follows ``contract/data_contract.md``: Synthea CSV under ``raw/<site>/csv`` and the four contract tables
under ``omop/<site>``, with the columns, order and concept IDs fixed by sections 4 and 5 of that document.
"""

from __future__ import annotations

import subprocess
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
JAR = HERE / "synthea-3.3.0.jar"
JAR_URL = "https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar"
POPULATION = 1000

EHR_RECORD = 32817
NO_MATCHING_CONCEPT = 0
GENDER = {"M": 8507, "F": 8532}
MEASUREMENT = {"39156-5": 3038553, "8480-6": 3004249}
UNIT = {"kg/m2": 9531, "mm[Hg]": 8876}
CONDITION = {"44054006": 201826}


@dataclass(frozen=True)
class Site:
    name: str
    state: str
    seed: int


SITES: tuple[Site, ...] = (
    Site("site_a", "Massachusetts", 101),
    Site("site_b", "California", 102),
    Site("site_c", "Texas", 103),
    Site("site_d", "New York", 104),
    Site("site_e", "Florida", 105),
)


def ensure_synthea() -> Path:
    if not JAR.exists():
        urllib.request.urlretrieve(JAR_URL, JAR)
    return JAR


def generate(site: Site, population: int = POPULATION) -> Path:
    out = HERE / "raw" / site.name
    subprocess.run(
        [
            "java",
            "-jar",
            str(ensure_synthea()),
            "-p",
            str(population),
            "-s",
            str(site.seed),
            "-cs",
            str(site.seed),
            "--exporter.csv.export",
            "true",
            "--exporter.fhir.export",
            "false",
            "--exporter.baseDirectory",
            str(out),
            site.state,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return out / "csv"


def _date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="mixed", utc=True).dt.strftime("%Y-%m-%d")


def to_omop(csv: Path, out: Path) -> Mapping[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    patients = pd.read_csv(csv / "patients.csv", dtype=str)

    person = pd.DataFrame(
        {
            "person_id": range(1, len(patients) + 1),
            "gender_concept_id": patients["GENDER"].map(GENDER).fillna(NO_MATCHING_CONCEPT).astype(int),
            "year_of_birth": patients["BIRTHDATE"].str.slice(0, 4).astype(int),
            "race_concept_id": NO_MATCHING_CONCEPT,
            "ethnicity_concept_id": NO_MATCHING_CONCEPT,
            "person_source_value": patients["Id"],
        }
    )
    person.to_csv(out / "person.csv", index=False)
    key = person.set_index("person_source_value")["person_id"]

    encounters = pd.read_csv(csv / "encounters.csv", dtype=str)
    encounters = encounters[encounters["PATIENT"].isin(key.index)]
    spans = (
        encounters.assign(person_id=encounters["PATIENT"].map(key))
        .groupby("person_id", as_index=False)
        .agg(
            observation_period_start_date=("START", "min"),
            observation_period_end_date=("STOP", "max"),
        )
    )
    spans = spans.sort_values("person_id").reset_index(drop=True)
    observation_period = pd.DataFrame(
        {
            "observation_period_id": range(1, len(spans) + 1),
            "person_id": spans["person_id"],
            "observation_period_start_date": _date(spans["observation_period_start_date"]),
            "observation_period_end_date": _date(spans["observation_period_end_date"]),
            "period_type_concept_id": EHR_RECORD,
        }
    )
    observation_period.to_csv(out / "observation_period.csv", index=False)

    observations = pd.read_csv(csv / "observations.csv", dtype=str)
    observations = observations[observations["CODE"].isin(MEASUREMENT) & observations["PATIENT"].isin(key.index)]
    observations = observations.reset_index(drop=True)
    measurement = pd.DataFrame(
        {
            "measurement_id": range(1, len(observations) + 1),
            "person_id": observations["PATIENT"].map(key),
            "measurement_concept_id": observations["CODE"].map(MEASUREMENT),
            "measurement_date": _date(observations["DATE"]),
            "measurement_type_concept_id": EHR_RECORD,
            "value_as_number": pd.to_numeric(observations["VALUE"], errors="coerce"),
            "unit_concept_id": observations["UNITS"].map(UNIT).fillna(NO_MATCHING_CONCEPT).astype(int),
            "measurement_source_value": observations["CODE"],
        }
    )
    measurement.to_csv(out / "measurement.csv", index=False)

    conditions = pd.read_csv(csv / "conditions.csv", dtype=str)
    conditions = conditions[conditions["CODE"].isin(CONDITION) & conditions["PATIENT"].isin(key.index)]
    conditions = conditions.reset_index(drop=True)
    condition_occurrence = pd.DataFrame(
        {
            "condition_occurrence_id": range(1, len(conditions) + 1),
            "person_id": conditions["PATIENT"].map(key),
            "condition_concept_id": conditions["CODE"].map(CONDITION),
            "condition_start_date": _date(conditions["START"]),
            "condition_type_concept_id": EHR_RECORD,
            "condition_source_value": conditions["CODE"],
        }
    )
    condition_occurrence.to_csv(out / "condition_occurrence.csv", index=False)

    return {
        "person": len(person),
        "observation_period": len(observation_period),
        "measurement": len(measurement),
        "condition_occurrence": len(condition_occurrence),
        "diabetes_patients": int(condition_occurrence["person_id"].nunique()),
    }


if __name__ == "__main__":
    for site in SITES:
        counts = to_omop(generate(site), HERE / "omop" / site.name)
        print(f"{site.name} ({site.state}): {counts}", flush=True)
