"""Generate a multi-site Synthea cohort and map each site to the OMOP tables of the data contract.

A site is one Synthea population and one NVFlare client, so no site ever sees another's patients.
Sites differ in age range, size and gender, which drives T2DM prevalence from 1.1% to 17.4% and makes the split non-IID.

Synthea CSV goes to ``data/source/<site>/csv`` and the contract tables to ``data/omop/<site>``.
Sections 4 and 5 of ``contract/data_contract.md`` fix the columns, their order and the concept IDs.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
JAR = HERE / "synthea-3.3.0.jar"
JAR_URL = "https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar"

EHR_RECORD = 32817
NO_MATCHING_CONCEPT = 0
GENDER = {"M": 8507, "F": 8532}
MEASUREMENT = {"39156-5": 3038553, "8480-6": 3004249}
UNIT = {"kg/m2": 9531, "mm[Hg]": 8876}
CONDITION = {"44054006": 201826}


@dataclass(frozen=True)
class Site:
    state: str
    ages: str
    population: int
    seed: int
    gender: str | None = None


def _named(sites: Sequence[Site]) -> Mapping[str, Site]:
    return {f"site_{chr(ord('a') + i)}": site for i, site in enumerate(sites)}


SITES: Mapping[str, Site] = _named(
    (
        Site("Illinois", "18-40", 1200, 401),
        Site("Washington", "35-65", 900, 402),
        Site("Arizona", "65-95", 600, 403),
        Site("Colorado", "40-80", 400, 404),
        Site("Oregon", "25-60", 700, 405, gender="F"),
    )
)


def ensure_synthea() -> Path:
    if not JAR.exists():
        urllib.request.urlretrieve(JAR_URL, JAR)
    return JAR


def generate(name: str, site: Site) -> Path:
    out = DATA / "source" / name
    command = [
        "java",
        "-jar",
        str(ensure_synthea()),
        "-p",
        str(site.population),
        "-s",
        str(site.seed),
        "-cs",
        str(site.seed),
        "-a",
        site.ages,
        *(("-g", site.gender) if site.gender else ()),
        "--exporter.csv.export",
        "true",
        "--exporter.fhir.export",
        "false",
        "--exporter.baseDirectory",
        str(out),
        site.state,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
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
        "persons": len(person),
        "measurements": len(measurement),
        "diabetes_patients": int(condition_occurrence["person_id"].nunique()),
    }


def compact_source(csv: Path, out: Path) -> Mapping[str, Mapping[str, int | str]]:
    """Keep only the rows the OMOP mapping reads, gzipped, so the cohort is reproducible without the full export."""
    out.mkdir(parents=True, exist_ok=True)
    frames = {
        "patients.csv": pd.read_csv(csv / "patients.csv", dtype=str),
        "encounters.csv": pd.read_csv(csv / "encounters.csv", dtype=str)[["START", "STOP", "PATIENT"]],
    }
    observations = pd.read_csv(csv / "observations.csv", dtype=str)
    frames["observations.csv"] = observations[observations["CODE"].isin(MEASUREMENT)]
    conditions = pd.read_csv(csv / "conditions.csv", dtype=str)
    frames["conditions.csv"] = conditions[conditions["CODE"].isin(CONDITION)]

    manifest: dict[str, Mapping[str, int | str]] = {}
    for name, frame in frames.items():
        path = out / f"{name}.gz"
        with gzip.open(path, "wt", newline="") as handle:
            frame.to_csv(handle, index=False)
        manifest[f"{name}.gz"] = {
            "compressed_bytes": path.stat().st_size,
            "rows_excluding_header": len(frame),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return manifest


if __name__ == "__main__":
    summary: dict[str, object] = {}
    for name, site in SITES.items():
        csv = generate(name, site)
        counts = to_omop(csv, DATA / "omop" / name)
        files = compact_source(csv, DATA / "source_compact" / name)
        summary[name] = {
            "state": site.state,
            "ages": site.ages,
            "gender": site.gender or "M+F",
            "seed": site.seed,
            **counts,
            "t2dm_prevalence": round(counts["diabetes_patients"] / counts["persons"], 4),
            "files": files,
        }
        print(f"  {name} {site.state} ages {site.ages}: {counts['persons']} persons", flush=True)
    (DATA / "source_compact" / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
