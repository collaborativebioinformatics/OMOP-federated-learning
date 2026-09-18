#!/usr/bin/env python3
"""Map the selected UKB synthetic source extract to a four-table OMOP pilot.

This is a narrow mapping test, not a complete UKB ETL. E11 subcodes are
deliberately rolled up to the standard type 2 diabetes concept, losing their
complication detail. Other ICD-10 codes remain traceable with concept ID 0.
"""

import argparse
import collections
import csv
import datetime as dt
import json
import re
from pathlib import Path


CONCEPTS = {
    8507: ("Gender", "Gender", "M"),
    8532: ("Gender", "Gender", "F"),
    3038553: ("Measurement", "LOINC", "39156-5"),
    3004249: ("Measurement", "LOINC", "8480-6"),
    9531: ("Unit", "UCUM", "kg/m2"),
    8876: ("Unit", "UCUM", "mm[Hg]"),
    201826: ("Condition", "SNOMED", "44054006"),
}
FIELDS = {31, 34, 53, 21001, 4080, 41270, 41280}
COLUMN = re.compile(r"^(\d+)-(\d+)\.(\d+)$")
TABLES = {
    "person": ["person_id", "gender_concept_id", "year_of_birth",
               "race_concept_id", "ethnicity_concept_id", "person_source_value"],
    "observation_period": ["observation_period_id", "person_id",
                           "observation_period_start_date", "observation_period_end_date",
                           "period_type_concept_id"],
    "measurement": ["measurement_id", "person_id", "measurement_concept_id",
                    "measurement_date", "measurement_type_concept_id", "value_as_number",
                    "unit_concept_id", "measurement_source_value"],
    "condition_occurrence": ["condition_occurrence_id", "person_id",
                             "condition_concept_id", "condition_start_date",
                             "condition_type_concept_id", "condition_source_value"],
}


def verify_vocabulary(vocabulary_dir):
    path = vocabulary_dir / "CONCEPT.csv"
    found = {}
    with path.open(encoding="utf-8", newline="") as source:
        for record in csv.DictReader(source, delimiter="\t"):
            concept_id = int(record["concept_id"])
            if concept_id in CONCEPTS:
                found[concept_id] = record
            if len(found) == len(CONCEPTS):
                break
    for concept_id, expected in CONCEPTS.items():
        record = found.get(concept_id)
        if record is None:
            raise ValueError(f"Athena CONCEPT.csv has no concept {concept_id}")
        actual = (record["domain_id"], record["vocabulary_id"], record["concept_code"])
        if actual != expected or record["standard_concept"] != "S" or record["invalid_reason"]:
            raise ValueError(f"Athena concept {concept_id} is not the expected valid standard concept: {record}")
    versions = {}
    with (vocabulary_dir / "VOCABULARY.csv").open(encoding="utf-8", newline="") as source:
        for record in csv.DictReader(source, delimiter="\t"):
            if record["vocabulary_id"] in {"LOINC", "SNOMED", "UCUM", "Gender"}:
                versions[record["vocabulary_id"]] = record["vocabulary_version"]
    return versions


def parse_date(value, counter, kind):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        counter[f"invalid_{kind}_date"] += 1
        return None


def source_columns(header):
    columns = collections.defaultdict(list)
    for name in header:
        match = COLUMN.fullmatch(name)
        if match and int(match.group(1)) in FIELDS:
            field_id, instance, array = map(int, match.groups())
            columns[field_id].append((instance, array, name))
    missing = FIELDS - columns.keys()
    if missing:
        raise ValueError(f"Source extract lacks UKB fields: {sorted(missing)}")
    for entries in columns.values():
        entries.sort()
    return columns


def make_tables(source_path, e11_only=False):
    with source_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        if not reader.fieldnames or reader.fieldnames[0].lower() != "eid":
            raise ValueError("Source must start with EID")
        columns = source_columns(reader.fieldnames)
        people = list(reader)
    if len({person["EID"] for person in people}) != len(people):
        raise ValueError("Source contains duplicate EIDs")
    people.sort(key=lambda person: int(person["EID"]))

    output = {name: [] for name in TABLES}
    counts = collections.Counter()
    unmapped_codes = collections.Counter()
    missing_date_columns = set()
    assessment_dates = {(i, a): name for i, a, name in columns[53]}
    diagnosis_dates = {(i, a): name for i, a, name in columns[41280]}
    for person_id, person in enumerate(people, start=1):
        eid = person["EID"]
        sex = person[columns[31][0][2]].strip()
        year = person[columns[34][0][2]].strip()
        if not (year.isdigit() and len(year) == 4):
            raise ValueError(f"Invalid birth year for EID {eid}: {year!r}")
        gender = {"0": 8532, "1": 8507}.get(sex, 0)
        if gender == 0:
            counts["unknown_sex"] += 1
        output["person"].append([person_id, gender, year, 0, 0, eid])
        event_dates = []

        for field_id, concept_id, unit_id in ((21001, 3038553, 9531), (4080, 3004249, 8876)):
            for instance, array, name in columns[field_id]:
                value = person[name].strip()
                if not value:
                    continue
                date_name = assessment_dates.get((instance, 0))
                date = parse_date(person[date_name].strip(), counts, "assessment") if date_name else None
                if date is None:
                    counts["measurement_without_assessment_date"] += 1
                    continue
                try:
                    number = float(value)
                    if not (-float("inf") < number < float("inf")):
                        raise ValueError
                except ValueError:
                    counts["invalid_measurement_value"] += 1
                    continue
                output["measurement"].append([
                    len(output["measurement"]) + 1, person_id, concept_id, date,
                    0, value, unit_id, str(field_id),
                ])
                event_dates.append(date)

        for instance, array, name in columns[41270]:
            code = person[name].strip()
            if not code:
                continue
            is_e11 = code.upper().replace(".", "").startswith("E11")
            if e11_only and not is_e11:
                continue
            date_name = diagnosis_dates.get((instance, array))
            if date_name is None:
                counts["diagnosis_without_date_column"] += 1
                missing_date_columns.add(f"41280-{instance}.{array}")
                continue
            date = parse_date(person[date_name].strip(), counts, "diagnosis")
            if date is None:
                counts["diagnosis_without_paired_date"] += 1
                continue
            # Broad pilot rollup: complications encoded by E11 subcodes are lost.
            concept_id = 201826 if is_e11 else 0
            if concept_id == 0:
                unmapped_codes[code] += 1
            else:
                counts["e11_rollup_records"] += 1
            output["condition_occurrence"].append([
                len(output["condition_occurrence"]) + 1, person_id,
                concept_id, date, 0, code,
            ])
            event_dates.append(date)

        if event_dates:
            output["observation_period"].append([
                len(output["observation_period"]) + 1, person_id,
                min(event_dates), max(event_dates), 0,
            ])
        else:
            counts["people_without_dated_events"] += 1

    report = {
        "source": str(source_path),
        "diagnosis_scope": "e11_only" if e11_only else "all_dated_codes",
        "table_rows": {name: len(rows) for name, rows in output.items()},
        "counts": dict(sorted(counts.items())),
        "unmapped_diagnosis_records": sum(unmapped_codes.values()),
        "unmapped_diagnosis_distinct_codes": len(unmapped_codes),
        "unmapped_diagnosis_examples": unmapped_codes.most_common(20),
        "diagnosis_date_columns_absent": sorted(missing_date_columns),
        "mapping_notes": [
            "E11 and all subcodes are broadly rolled up to standard SNOMED 201826; complication detail is lost.",
            ("Only E11-family diagnoses are exported in this focused pilot."
             if e11_only else
             "Other ICD-10 diagnosis codes retain their source values with condition_concept_id 0."),
            "Type concept IDs are 0 because the source provenance has not been mapped to a type concept.",
            "Observation periods span the selected dated events and are technical proxies, not verified UKB enrollment.",
            "The four output tables are a pilot subset of OMOP CDM 5.4, not a complete CDM instance.",
        ],
    }
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/ukb_pilot/ukb_subset.tsv"))
    parser.add_argument("--vocabulary", type=Path, default=Path("../OMOP_complete"))
    parser.add_argument("--output", type=Path, default=Path("data/ukb_omop_pilot/all"))
    parser.add_argument("--e11-only", action="store_true", help="Export only E11-family diagnosis records")
    args = parser.parse_args()
    versions = verify_vocabulary(args.vocabulary)
    tables, report = make_tables(args.input, args.e11_only)
    report["vocabulary_versions"] = versions
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (args.output / f"{name}.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(TABLES[name])
            writer.writerows(rows)
    (args.output / "mapping_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
