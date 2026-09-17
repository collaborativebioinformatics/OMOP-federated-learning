#!/usr/bin/env python3
"""Create small, deterministic gzip archives sufficient to rebuild the cohort."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from datetime import date
from pathlib import Path

from build_cohort import BIOMARKERS, T2D_CODE


def read_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def is_numeric(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def write_gzip_csv(path: Path, fieldnames: list[str], rows) -> int:
    """Write reproducible gzip (fixed timestamp and no embedded source filename)."""
    count = 0
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
                    count += 1
    return count


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact(source: Path, cohort: Path, output: Path, window_days: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cohort_patients = {row["patient_id"] for row in read_rows(cohort / "patients.csv")}

    condition_path = source / "conditions.csv"
    with condition_path.open(newline="", encoding="utf-8-sig") as handle:
        condition_fields = csv.DictReader(handle).fieldnames
    if not condition_fields:
        raise ValueError("conditions.csv has no header")
    condition_rows = [
        row
        for row in read_rows(condition_path)
        if row["PATIENT"] in cohort_patients and row.get("CODE", "").strip() == T2D_CODE
    ]
    diagnosis_dates: dict[str, date] = {}
    for row in condition_rows:
        diagnosed = parse_date(row["START"])
        patient_id = row["PATIENT"]
        if patient_id not in diagnosis_dates or diagnosed < diagnosis_dates[patient_id]:
            diagnosis_dates[patient_id] = diagnosed

    patient_path = source / "patients.csv"
    with patient_path.open(newline="", encoding="utf-8-sig") as handle:
        patient_fields = csv.DictReader(handle).fieldnames
    observation_path = source / "observations.csv"
    with observation_path.open(newline="", encoding="utf-8-sig") as handle:
        observation_fields = csv.DictReader(handle).fieldnames
    if not patient_fields or not observation_fields:
        raise ValueError("A source CSV has no header")

    patient_rows = (row for row in read_rows(patient_path) if row["Id"] in cohort_patients)

    def selected_observations():
        for row in read_rows(observation_path):
            patient_id = row["PATIENT"]
            if patient_id not in cohort_patients or row.get("CODE", "").strip() not in BIOMARKERS:
                continue
            if not is_numeric(row.get("VALUE", "")):
                continue
            measured = parse_date(row["DATE"])
            if abs((measured - diagnosis_dates[patient_id]).days) <= window_days:
                yield row

    files = {
        "patients.csv.gz": (patient_fields, patient_rows),
        "conditions.csv.gz": (condition_fields, iter(condition_rows)),
        "observations.csv.gz": (observation_fields, selected_observations()),
    }
    manifest = {
        "description": "Filtered Synthea source sufficient to reproduce the committed diabetes cohort",
        "filters": {
            "patients": len(cohort_patients),
            "condition_code": T2D_CODE,
            "observation_codes": sorted(BIOMARKERS),
            "observation_window_days": window_days,
            "numeric_observations_only": True,
        },
        "files": {},
    }
    for name, (fields, records) in files.items():
        destination = output / name
        count = write_gzip_csv(destination, fields, records)
        manifest["files"][name] = {
            "rows_excluding_header": count,
            "compressed_bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }

    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=here / "data" / "source")
    parser.add_argument("--cohort", type=Path, default=here / "data" / "cohort")
    parser.add_argument("--output", type=Path, default=here / "data" / "source_compact")
    parser.add_argument("--window-days", type=int, default=365)
    args = parser.parse_args()
    manifest = compact(args.source, args.cohort, args.output, args.window_days)
    total = sum(item["compressed_bytes"] for item in manifest["files"].values())
    print(f"Wrote {sum(item['rows_excluding_header'] for item in manifest['files'].values())} rows")
    print(f"Compressed size: {total / 1024:.1f} KiB -> {args.output}")


if __name__ == "__main__":
    main()
