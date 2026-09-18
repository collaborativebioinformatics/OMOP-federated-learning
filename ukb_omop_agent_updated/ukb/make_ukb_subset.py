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


def index_files(source_dir, fields=FIELDS):
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
                if match and int(match.group(1)) in fields:
                    if name in seen_columns:
                        raise ValueError(f"Duplicate UKB column {name} in {path}")
                    seen_columns.add(name)
                    found_fields.add(int(match.group(1)))
                    selected.append((index, name))
            if selected:
                indexed.append((path, selected))
    missing = set(fields) - found_fields
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


def normalize_code(code):
    return code.strip().upper().replace(".", "")


def has_matching_diagnosis(row, indices, codes, prefixes):
    for index in indices:
        if index >= len(row):
            continue
        code = normalize_code(row[index])
        if code and (code in codes or any(code.startswith(prefix) for prefix in prefixes)):
            return True
    return False


def choose_eids(indexed, participant_count, case_target, all_matching=False,
                codes=(), prefixes=("E11",)):
    selected = []
    selected_set = set()
    if case_target or all_matching:
        for path, columns in indexed:
            diagnosis_indices = [
                index for index, name in columns
                if name.startswith("41270-")
            ]
            if not diagnosis_indices:
                continue
            for row in rows_from(path):
                eid = row[0].strip()
                if eid and eid not in selected_set and has_matching_diagnosis(
                    row, diagnosis_indices, codes, prefixes
                ):
                    selected.append(eid)
                    selected_set.add(eid)
                    if not all_matching and len(selected) >= case_target:
                        break
            if not all_matching and len(selected) >= case_target:
                break
    case_count = len(selected)
    if all_matching:
        if not selected:
            raise ValueError("No participants match the selected diagnosis codes in the supplied files")
        return selected, case_count
    if case_target == participant_count and case_count < case_target:
        raise ValueError(
            f"Only {case_count} matching participants found; need {case_target}. "
            "Sample more UKB rows before building a diagnosis-only cohort."
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


def column_order(name, fields=FIELDS):
    match = COLUMN_RE.fullmatch(name)
    return (fields.index(int(match.group(1))), int(match.group(2)), int(match.group(3)))


def make_subset(source_dir, output_dir, participant_count, case_target, all_e11=False,
                all_matching=False, diagnosis_codes=(), diagnosis_prefixes=(), fields=FIELDS):
    fields = tuple(dict.fromkeys(fields))
    if not fields or any(field <= 0 for field in fields):
        raise ValueError("Require one or more positive UKB field IDs")
    if all_e11 and (all_matching or diagnosis_codes or diagnosis_prefixes):
        raise ValueError("Use --all-e11 alone, or --all-matching with diagnosis filters")
    codes = tuple(normalize_code(code) for code in diagnosis_codes)
    prefixes = tuple(normalize_code(prefix) for prefix in diagnosis_prefixes)
    if not codes and not prefixes and (all_e11 or all_matching or case_target):
        prefixes = ("E11",)
    for code in (*codes, *prefixes):
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", code):
            raise ValueError(f"Invalid ICD-10 code or prefix: {code!r}")
    select_all = all_e11 or all_matching
    indexed = index_files(source_dir, fields)
    selected, case_count = choose_eids(indexed, participant_count, case_target,
                                       select_all, codes, prefixes)
    selected_set = set(selected)
    columns = sorted(
        (name for _, file_columns in indexed for _, name in file_columns),
        key=lambda name: column_order(name, fields),
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
                if index < len(row) and row[index]:
                    values[eid][name] = row[index]
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
        "purpose": "UKB synthetic source subset for OMOP mapping tests",
        "participants": len(selected),
        "matching_diagnosis_participants_selected": case_count,
        "diagnosis_codes": list(codes),
        "diagnosis_prefixes": list(prefixes),
        "selection": ("all matching participants" if select_all else
                      "diagnosis-enriched participant sample" if case_target else
                      "first participants in input sample"),
        "field_ids": list(fields),
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
    parser.add_argument("--cases", type=int, default=20, help="Target diagnosis-positive participants")
    parser.add_argument("--all-e11", action="store_true", help="Select every E11-positive EID in the input, without filling with other EIDs")
    parser.add_argument("--all-matching", action="store_true", help="Select every EID matching the diagnosis filters")
    parser.add_argument("--fields", type=int, nargs="+", default=list(FIELDS),
                        help="UKB field IDs to include; defaults to the original pilot fields")
    parser.add_argument("--diagnosis-code", action="append", default=[], help="Exact ICD-10 code; repeatable")
    parser.add_argument("--diagnosis-prefix", action="append", default=[], help="ICD-10 family prefix; repeatable")
    args = parser.parse_args()
    if args.all_e11 and (args.all_matching or args.diagnosis_code or args.diagnosis_prefix):
        parser.error("Use --all-e11 alone, or --all-matching with diagnosis filters")
    if not (args.all_e11 or args.all_matching) and (args.participants < 1 or not 0 <= args.cases <= args.participants):
        parser.error("Require participants >= 1 and 0 <= cases <= participants")
    print(json.dumps(
        make_subset(args.input, args.output, args.participants, args.cases,
                    args.all_e11, args.all_matching, args.diagnosis_code,
                    args.diagnosis_prefix, args.fields), indent=2
    ))


if __name__ == "__main__":
    main()
