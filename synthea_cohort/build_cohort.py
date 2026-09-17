#!/usr/bin/env python3
"""Convert Synthea CSV exports into linked cohort, diagnosis, and biomarker tables."""

from __future__ import annotations

import argparse
import csv
import math
from datetime import date, datetime
from pathlib import Path


T2D_CODE = "44054006"

# The fallback IDs below are standard concepts verified in published OHDSI/HL7
# artifacts. An Athena CONCEPT.csv supplied with --omop-concepts takes precedence.
# Unverified codes intentionally resolve to 0, OMOP's unmapped concept convention.
FALLBACK_OMOP_IDS = {
    ("SNOMED", T2D_CODE): 201826,
    ("LOINC", "4548-4"): 3004410,
    ("LOINC", "2339-0"): 3000483,
    ("LOINC", "39156-5"): 3038553,
    ("LOINC", "8480-6"): 3004249,
}

BIOMARKERS = {
    "4548-4": "hba1c",
    "2339-0": "glucose_blood",
    "2345-7": "glucose_serum_plasma",
    "39156-5": "bmi",
    "8480-6": "systolic_bp",
    "33914-3": "egfr",
    "38483-4": "creatinine_blood",
    "2160-0": "creatinine_serum_plasma",
}


def read_rows(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Required file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}") from exc


def parse_number(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def age_on(birth: date, event_date: date) -> float:
    return round((event_date - birth).days / 365.2425, 2)


def load_omop_concepts(path: Path | None) -> dict[tuple[str, str], int]:
    """Load standard, valid concepts from an Athena CONCEPT.csv/CONCEPT.tsv."""
    result = dict(FALLBACK_OMOP_IDS)
    if path is None:
        return result
    if not path.is_file():
        raise FileNotFoundError(f"OMOP concept file not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        wanted = {("SNOMED", T2D_CODE), *(('LOINC', code) for code in BIOMARKERS)}
        for row in reader:
            key = (row.get("vocabulary_id", "").strip(), row.get("concept_code", "").strip())
            if key not in wanted:
                continue
            if row.get("standard_concept", "").strip() != "S":
                continue
            if row.get("invalid_reason", "").strip():
                continue
            result[key] = int(row["concept_id"])
    return result


def write_table(path: Path, fields: list[str], records: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def build(args: argparse.Namespace) -> dict[str, int]:
    concepts = load_omop_concepts(args.omop_concepts)
    diagnosis_rows: list[dict[str, object]] = []
    diagnoses_by_patient: dict[str, list[dict[str, object]]] = {}

    for source in read_rows(args.input / "conditions.csv"):
        if source.get("CODE", "").strip() != T2D_CODE:
            continue
        diagnosis_date = parse_date(source.get("START", ""))
        if diagnosis_date is None:
            continue
        patient_id = source["PATIENT"].strip()
        record = {
            "diagnosis_id": f"condition-{len(diagnosis_rows) + 1}",
            "patient_id": patient_id,
            "encounter_id": source.get("ENCOUNTER", "").strip(),
            "diagnosis_start_date": diagnosis_date.isoformat(),
            "diagnosis_end_date": (parse_date(source.get("STOP", "")) or ""),
            "diagnosis_name": source.get("DESCRIPTION", "").strip(),
            "source_vocabulary": "SNOMED",
            "source_code": T2D_CODE,
            "omop_condition_concept_id": concepts.get(("SNOMED", T2D_CODE), 0),
        }
        if isinstance(record["diagnosis_end_date"], date):
            record["diagnosis_end_date"] = record["diagnosis_end_date"].isoformat()
        diagnosis_rows.append(record)
        diagnoses_by_patient.setdefault(patient_id, []).append(record)

    first_diagnosis = {
        patient_id: min(parse_date(str(row["diagnosis_start_date"])) for row in records)
        for patient_id, records in diagnoses_by_patient.items()
    }

    patient_rows: list[dict[str, object]] = []
    eligible_patients: set[str] = set()
    for source in read_rows(args.input / "patients.csv"):
        patient_id = source["Id"].strip()
        index_date = first_diagnosis.get(patient_id)
        if index_date is None or index_date > args.study_end:
            continue
        birth_date = parse_date(source.get("BIRTHDATE", ""))
        death_date = parse_date(source.get("DEATHDATE", ""))
        if birth_date is None:
            continue
        death_event = int(death_date is not None and index_date <= death_date <= args.study_end)
        followup_end = death_date if death_event else args.study_end
        if followup_end < index_date:
            continue
        eligible_patients.add(patient_id)
        patient_rows.append({
            "patient_id": patient_id,
            "birth_date": birth_date.isoformat(),
            "sex": source.get("GENDER", "").strip(),
            "race": source.get("RACE", "").strip(),
            "ethnicity": source.get("ETHNICITY", "").strip(),
            "index_date": index_date.isoformat(),
            "age_at_index": age_on(birth_date, index_date),
            "death_event": death_event,
            "death_date": death_date.isoformat() if death_date else "",
            "followup_end_date": followup_end.isoformat(),
            "survival_time_days": (followup_end - index_date).days,
        })

    diagnosis_rows = [row for row in diagnosis_rows if str(row["patient_id"]) in eligible_patients]

    biomarker_rows: list[dict[str, object]] = []
    for source in read_rows(args.input / "observations.csv"):
        patient_id = source["PATIENT"].strip()
        code = source.get("CODE", "").strip()
        measured = parse_date(source.get("DATE", ""))
        value = parse_number(source.get("VALUE", ""))
        if patient_id not in eligible_patients or code not in BIOMARKERS or measured is None or value is None:
            continue
        index_date = first_diagnosis[patient_id]
        days_from_index = (measured - index_date).days
        if abs(days_from_index) > args.window_days:
            continue
        biomarker_rows.append({
            "measurement_id": f"measurement-{len(biomarker_rows) + 1}",
            "patient_id": patient_id,
            "encounter_id": source.get("ENCOUNTER", "").strip(),
            "measurement_date": measured.isoformat(),
            "days_from_index": days_from_index,
            "biomarker_name": BIOMARKERS[code],
            "value_as_number": format(value, ".10g"),
            "unit_source_value": source.get("UNITS", "").strip(),
            "source_vocabulary": "LOINC",
            "source_code": code,
            "source_description": source.get("DESCRIPTION", "").strip(),
            "omop_measurement_concept_id": concepts.get(("LOINC", code), 0),
        })

    args.output.mkdir(parents=True, exist_ok=True)
    write_table(args.output / "patients.csv", [
        "patient_id", "birth_date", "sex", "race", "ethnicity", "index_date",
        "age_at_index", "death_event", "death_date", "followup_end_date", "survival_time_days",
    ], patient_rows)
    write_table(args.output / "diagnoses.csv", [
        "diagnosis_id", "patient_id", "encounter_id", "diagnosis_start_date",
        "diagnosis_end_date", "diagnosis_name", "source_vocabulary", "source_code",
        "omop_condition_concept_id",
    ], diagnosis_rows)
    write_table(args.output / "biomarkers.csv", [
        "measurement_id", "patient_id", "encounter_id", "measurement_date",
        "days_from_index", "biomarker_name", "value_as_number", "unit_source_value",
        "source_vocabulary", "source_code", "source_description",
        "omop_measurement_concept_id",
    ], biomarker_rows)
    return {"patients": len(patient_rows), "diagnoses": len(diagnosis_rows), "biomarkers": len(biomarker_rows)}


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Synthea output/csv directory")
    parser.add_argument("--output", type=Path, required=True, help="Directory for normalized CSVs")
    parser.add_argument("--study-end", required=True, type=lambda v: datetime.strptime(v, "%Y-%m-%d").date())
    parser.add_argument("--window-days", type=int, default=365, help="Days either side of diagnosis")
    parser.add_argument("--omop-concepts", type=Path, help="Athena CONCEPT.csv or CONCEPT.tsv")
    args = parser.parse_args()
    if args.window_days < 0:
        parser.error("--window-days must be non-negative")
    return args


if __name__ == "__main__":
    arguments = cli()
    counts = build(arguments)
    print("Wrote " + ", ".join(f"{count} {name}" for name, count in counts.items()))
