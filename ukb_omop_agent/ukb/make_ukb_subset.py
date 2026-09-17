#!/usr/bin/env python3
"""Create a small UK Biobank synthetic source extract for the OMOP pilot.

Input: official UKB synthetic tabular TSV files (plain or .gz) in one folder.
Output: one wide TSV with selected EIDs/fields and a JSON manifest.
This extracts UKB source data; it does not perform OMOP mapping.
"""

import argparse
import csv
import gzip
import json
import re
from pathlib import Path


# Data contract inputs: sex, year of birth, assessment date, BMI,
# automated systolic BP, ICD-10 diagnoses and the paired diagnosis dates.
FIELDS = (31, 34, 53, 21001, 4080, 41270, 41280)
COLUMN_RE = re.compile(r"^(\d+)-(\d+)\.(\d+)$")


def open_text(path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open("r", encoding="utf-8-sig", newline="")


def tsv_reader(path):
    with open_text(path) as source:
        reader = csv.reader(source, delimiter="\t")
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Empty input file: {path}")
        if header[0].strip().lower() != "eid":
            raise ValueError(f"First column must be EID: {path}")
        yield header, reader


def index_files(source_dir):
    paths = sorted(
        path for path in source_dir.rglob("*")
        if path.is_file() and (path.name.endswith(".tsv") or path.name.endswith(".tsv.gz"))
    )
    if not paths:
        raise ValueError(f"No .tsv or .tsv.gz files under {source_dir}")

    indexed = []
    seen_columns = set()
    found_fields = set()
    for path in paths:
        for header, _ in tsv_reader(path):
            selected = []
            for index, name in enumerate(header[1:], start=1):
                match = COLUMN_RE.fullmatch(name.strip())
                if match and int(match.group(1)) in FIELDS:
                    if name in seen_columns:
                        raise ValueError(f"Duplicate UKB column {name} in {path}")
                    seen_columns.add(name)
                    found_fields.add(int(match.group(1)))
                    selected.append((index, name))
            if selected:
                indexed.append((path, selected))
    missing = set(FIELDS) - found_fields
    if missing:
        raise ValueError(
            f"Missing UKB fields {sorted(missing)}. Add the tabular files containing them."
        )
    return indexed


def rows_from(path):
    for _, reader in tsv_reader(path):
        for row in reader:
            if row:
                yield row


def has_type_2_diabetes(row, indices):
    for index in indices:
        if index >= len(row):
            continue
        # UKB field 41270 contains ICD-10 codes; E11 and its subcodes
        # indicate type 2 diabetes. The exact OMOP mapping is a later step.
        code = row[index].strip().upper().replace(".", "")
        if code.startswith("E11"):
            return True
    return False


def choose_eids(indexed, participant_count, case_target, all_e11=False):
    selected = []
    selected_set = set()
    if case_target or all_e11:
        for path, columns in indexed:
            diagnosis_indices = [
                index for index, name in columns
                if name.startswith("41270-")
            ]
            if not diagnosis_indices:
                continue
            for row in rows_from(path):
                eid = row[0].strip()
                if eid and eid not in selected_set and has_type_2_diabetes(row, diagnosis_indices):
                    selected.append(eid)
                    selected_set.add(eid)
                    if not all_e11 and len(selected) >= case_target:
                        break
            if not all_e11 and len(selected) >= case_target:
                break
    case_count = len(selected)
    if all_e11:
        if not selected:
            raise ValueError("No E11-positive participants found in the supplied files")
        return selected, case_count
    if case_target == participant_count and case_count < case_target:
        raise ValueError(
            f"Only {case_count} E11-positive participants found; need {case_target}. "
            "Sample more UKB rows before building an E11-only cohort."
        )
    if len(selected) == participant_count:
        return selected, case_count

    # All official UKB tabular field-group files contain the participant set.
    for row in rows_from(indexed[0][0]):
        eid = row[0].strip()
        if eid and eid not in selected_set:
            selected.append(eid)
            selected_set.add(eid)
        if len(selected) >= participant_count:
            break
    if len(selected) < participant_count:
        raise ValueError(f"Only found {len(selected)} of {participant_count} requested EIDs")
    return selected, case_count


def column_order(name):
    match = COLUMN_RE.fullmatch(name)
    return (FIELDS.index(int(match.group(1))), int(match.group(2)), int(match.group(3)))


def make_subset(source_dir, output_dir, participant_count, case_target, all_e11=False):
    indexed = index_files(source_dir)
    selected, case_count = choose_eids(indexed, participant_count, case_target, all_e11)
    selected_set = set(selected)
    columns = sorted(
        (name for _, file_columns in indexed for _, name in file_columns),
        key=column_order,
    )
    values = {eid: {} for eid in selected}
    source_files = []
    for path, file_columns in indexed:
        found = set()
        for row in rows_from(path):
            eid = row[0].strip()
            if eid not in selected_set:
                continue
            if eid in found:
                raise ValueError(f"Duplicate EID {eid} in {path}")
            found.add(eid)
            for index, name in file_columns:
                values[eid][name] = row[index] if index < len(row) else ""
            if len(found) == len(selected):
                break
        missing = selected_set - found
        if missing:
            raise ValueError(f"{path} is missing {len(missing)} selected EIDs")
        source_files.append(str(path))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "ukb_subset.tsv"
    with output_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, delimiter="\t")
        writer.writerow(["EID", *columns])
        for eid in selected:
            writer.writerow([eid, *(values[eid].get(name, "") for name in columns)])

    manifest = {
        "purpose": "UKB synthetic source subset for testing the four-table OMOP data contract",
        "participants": len(selected),
        "e11_positive_participants_selected": case_count,
        "selection": "all E11-positive participants" if all_e11 else "E11-enriched participant sample",
        "field_ids": list(FIELDS),
        "source_files": source_files,
        "columns": columns,
        "output": str(output_path),
        "note": "Preserves all present instances and arrays; no OMOP concept mapping performed.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory of UKB synthetic tabular TSVs")
    parser.add_argument("--output", type=Path, default=Path("data/ukb_pilot"))
    parser.add_argument("--participants", type=int, default=200)
    parser.add_argument("--cases", type=int, default=20, help="Target E11-positive participants")
    parser.add_argument("--all-e11", action="store_true", help="Select every E11-positive EID in the input, without filling with other EIDs")
    args = parser.parse_args()
    if not args.all_e11 and (args.participants < 1 or not 0 <= args.cases <= args.participants):
        parser.error("Require participants >= 1 and 0 <= cases <= participants")
    print(json.dumps(
        make_subset(args.input, args.output, args.participants, args.cases, args.all_e11), indent=2
    ))


if __name__ == "__main__":
    main()
