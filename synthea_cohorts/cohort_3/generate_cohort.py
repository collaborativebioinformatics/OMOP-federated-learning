#!/usr/bin/env python3
"""Create a large normalized demo cohort by resampling cohort_1 (no raw export)."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import random
import uuid
from collections import defaultdict
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def gzip_writer(path: Path, fieldnames: list[str]):
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9)
    text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
    writer = csv.DictWriter(text, fieldnames=fieldnames)
    writer.writeheader()
    return raw, compressed, text, writer


def close_writer(raw, compressed, text) -> None:
    text.close()
    if not compressed.closed:
        compressed.close()
    if not raw.closed:
        raw.close()


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=here.parent / "cohort_1" / "data" / "cohort")
    parser.add_argument("--output", type=Path, default=here / "data" / "cohort")
    parser.add_argument("--patients", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=20260918)
    args = parser.parse_args()

    patients = read_csv(args.template / "patients.csv")
    diagnoses = read_csv(args.template / "diagnoses.csv")
    biomarkers = read_csv(args.template / "biomarkers.csv")
    diagnoses_by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    biomarkers_by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in diagnoses:
        diagnoses_by_patient[row["patient_id"]].append(row)
    for row in biomarkers:
        biomarkers_by_patient[row["patient_id"]].append(row)

    args.output.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, rows in (("patients", patients), ("diagnoses", diagnoses), ("biomarkers", biomarkers)):
        files[name] = gzip_writer(args.output / f"{name}.csv.gz", list(rows[0]))

    rng = random.Random(args.seed)
    counts = {"patients": 0, "diagnoses": 0, "biomarkers": 0}
    try:
        for index in range(args.patients):
            source = patients[rng.randrange(len(patients))]
            source_id = source["patient_id"]
            patient_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthea-demo-cohort-2/patient/{index}"))
            patient = dict(source, patient_id=patient_id)
            files["patients"][3].writerow(patient)
            counts["patients"] += 1

            encounter_ids: dict[str, str] = {}
            for local_index, source_row in enumerate(diagnoses_by_patient[source_id]):
                old_encounter = source_row["encounter_id"]
                encounter_ids.setdefault(
                    old_encounter,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{patient_id}/encounter/{old_encounter}")),
                )
                row = dict(
                    source_row,
                    diagnosis_id=f"condition-{index + 1}-{local_index + 1}",
                    patient_id=patient_id,
                    encounter_id=encounter_ids[old_encounter],
                )
                files["diagnoses"][3].writerow(row)
                counts["diagnoses"] += 1

            for local_index, source_row in enumerate(biomarkers_by_patient[source_id]):
                old_encounter = source_row["encounter_id"]
                encounter_ids.setdefault(
                    old_encounter,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{patient_id}/encounter/{old_encounter}")),
                )
                value = float(source_row["value_as_number"])
                jittered = max(0.0, value * (1.0 + rng.gauss(0.0, 0.03)))
                row = dict(
                    source_row,
                    measurement_id=f"measurement-{index + 1}-{local_index + 1}",
                    patient_id=patient_id,
                    encounter_id=encounter_ids[old_encounter],
                    value_as_number=f"{jittered:.6g}",
                )
                files["biomarkers"][3].writerow(row)
                counts["biomarkers"] += 1
    finally:
        for raw, compressed, text, _ in files.values():
            close_writer(raw, compressed, text)

    try:
        template_label = str(args.template.resolve().relative_to(here.parent.resolve()))
    except ValueError:
        template_label = str(args.template)
    metadata = {
        "description": "Normalized demo cohort resampled from cohort_1; not a fresh Synthea population",
        "seed": args.seed,
        "template": template_label,
        "rows": counts,
        "biomarker_jitter_standard_deviation_fraction": 0.03,
        "raw_source_retained": False,
    }
    (here / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {counts['patients']:,} patients, {counts['diagnoses']:,} diagnoses, "
          f"and {counts['biomarkers']:,} biomarkers to {args.output}")


if __name__ == "__main__":
    main()
