#!/usr/bin/env python3
"""Legacy UKB diagnosis-only workflow; use general_agent.py for new work."""

import argparse
import collections
import csv
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path


REVIEW_COLUMNS = ["source_code", "records", "dated_records", "decision",
                  "candidate_concept_ids", "candidate_names", "candidate_evidence",
                  "target_concept_id", "mapping_kind", "evidence"]
FIELD = re.compile(r"^41270-(\d+)\.(\d+)$")
UKB_MAPPER = Path(__file__).resolve().parent / "ukb" / "map_ukb_to_omop.py"


def load_mapper():
    spec = importlib.util.spec_from_file_location("ukb_pilot_mapper", UKB_MAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def available_vocabularies(vocabulary):
    with (vocabulary / "VOCABULARY.csv").open(encoding="utf-8-sig", newline="") as stream:
        return {row["vocabulary_id"] for row in csv.DictReader(stream, delimiter="\t")}


def inventory(source_path):
    counts = collections.Counter()
    dated = collections.Counter()
    people = set()
    with source_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not reader.fieldnames or "EID" not in reader.fieldnames:
            raise ValueError("Expected a UKB TSV with an EID column")
        pairs = []
        for column in reader.fieldnames:
            match = FIELD.fullmatch(column)
            if match:
                date_column = f"41280-{match.group(1)}.{match.group(2)}"
                pairs.append((column, date_column if date_column in reader.fieldnames else None))
        if not pairs:
            raise ValueError("No UKB 41270 diagnosis columns found")
        for row in reader:
            eid = row["EID"].strip()
            if not eid or eid in people:
                raise ValueError(f"Missing or duplicate EID: {eid!r}")
            people.add(eid)
            for code_col, date_col in pairs:
                code = row[code_col].strip().upper()
                if not code:
                    continue
                counts[code] += 1
                if date_col and row[date_col].strip():
                    try:
                        dt.date.fromisoformat(row[date_col].strip()[:10])
                    except ValueError:
                        continue
                    dated[code] += 1
    return counts, dated, len(people), sum(1 for _, date_col in pairs if date_col is None)


def candidate_mappings(vocabulary, codes):
    """Find valid ICD10 Maps to standard Condition targets, never auto-approve."""
    wanted = collections.defaultdict(set)
    for code in codes:
        wanted[code.replace(".", "")].add(code)
    sources = {}
    with (vocabulary / "CONCEPT.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["vocabulary_id"] != "ICD10" or row["invalid_reason"]:
                continue
            code = row["concept_code"].upper().replace(".", "")
            if code in wanted:
                sources[int(row["concept_id"])] = wanted[code]
    if not sources:
        return {}
    targets = collections.defaultdict(set)
    with (vocabulary / "CONCEPT_RELATIONSHIP.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["relationship_id"] == "Maps to" and not row["invalid_reason"]:
                source_id = int(row["concept_id_1"])
                if source_id in sources:
                    for code in sources[source_id]:
                        targets[code].add(int(row["concept_id_2"]))
    target_ids = set().union(*targets.values()) if targets else set()
    if not target_ids:
        return {}
    metadata = {}
    with (vocabulary / "CONCEPT.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            value = row["concept_id"]
            if value.isdigit() and int(value) in target_ids:
                if row["standard_concept"] == "S" and not row["invalid_reason"] and row["domain_id"] == "Condition":
                    metadata[int(value)] = row["concept_name"]
    return {code: [(target, metadata[target]) for target in sorted(ids) if target in metadata]
            for code, ids in targets.items()}


def inspect(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output directory is not empty: {args.output}; choose a fresh directory")
    counts, dated, people, missing_pairs = inventory(args.input)
    vocabularies = available_vocabularies(args.vocabulary)
    candidates = candidate_mappings(args.vocabulary, counts) if "ICD10" in vocabularies else {}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "mapping_review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for code, count in counts.most_common():
            options = candidates.get(code, [])
            writer.writerow({"source_code": code, "records": count,
                             "dated_records": dated[code], "decision": "needs_review",
                             "candidate_concept_ids": ";".join(str(item[0]) for item in options),
                             "candidate_names": ";".join(item[1] for item in options),
                             "candidate_evidence": "ICD10 Maps to relationship" if options else "",
                             "target_concept_id": "", "mapping_kind": "", "evidence": ""})
    report = {
        "source": str(args.input), "participants": people,
        "diagnosis_records": sum(counts.values()),
        "dated_diagnosis_records": sum(dated.values()),
        "distinct_diagnosis_codes": len(counts),
        "diagnosis_columns_without_paired_date_column": missing_pairs,
        "source_vocabulary": "ICD10",
        "source_vocabulary_available": "ICD10" in vocabularies,
        "icd10cm_is_not_substituted": True,
        "codes_with_vocabulary_candidates": sum(bool(candidates.get(code)) for code in counts),
        "dated_records_with_condition_candidates": sum(dated[code] for code in counts if candidates.get(code)),
        "dated_record_candidate_fraction": (
            sum(dated[code] for code in counts if candidates.get(code)) / sum(dated.values())
            if dated else None
        ),
        "codes_with_multiple_condition_candidates": sum(len(candidates.get(code, [])) > 1 for code in counts),
        "automatic_mappings_applied": 0,
        "review_file": str(args.output / "mapping_review.csv"),
    }
    (args.output / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def read_review(path):
    approved = {}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(REVIEW_COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("Review CSV lacks required columns")
        for row in reader:
            code = row["source_code"].strip().upper()
            if not code or code in seen:
                raise ValueError(f"Missing or duplicate source code: {code!r}")
            seen.add(code)
            decision = row["decision"].strip().lower()
            if decision not in {"approved", "needs_review", "unmapped"}:
                raise ValueError(f"Invalid decision for {code}: {decision!r}")
            if decision != "approved":
                if row["target_concept_id"].strip():
                    raise ValueError(f"Unapproved code {code} has a target concept")
                continue
            kind = row["mapping_kind"].strip().lower()
            evidence = row["evidence"].strip()
            if kind not in {"exact", "broad_rollup", "vocabulary_maps_to"} or not evidence:
                raise ValueError(f"Approved code {code} needs mapping_kind and evidence")
            try:
                targets = [int(value.strip()) for value in row["target_concept_id"].split(";")]
            except ValueError as error:
                raise ValueError(f"Approved code {code} needs integer target IDs separated by semicolons") from error
            if any(target <= 0 for target in targets) or len(set(targets)) != len(targets):
                raise ValueError(f"Approved code {code} needs distinct positive targets")
            candidate_ids = {int(value) for value in row["candidate_concept_ids"].split(";") if value.strip()}
            if kind == "vocabulary_maps_to" and not set(targets).issubset(candidate_ids):
                raise ValueError(f"Approved vocabulary targets for {code} must be among its candidates")
            if code.replace(".", "").startswith("E11") and code.replace(".", "") != "E11" and targets == [201826] and kind == "exact":
                raise ValueError(f"E11 subcode {code} to 201826 must be marked broad_rollup")
            approved[code] = {"targets": targets, "kind": kind, "evidence": evidence}
    if not approved:
        raise ValueError("No approved mappings; review at least one code before apply")
    return approved


def validate_targets(vocabulary, approved):
    ids = {target for item in approved.values() for target in item["targets"]}
    found = {}
    with (vocabulary / "CONCEPT.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            value = row["concept_id"]
            if value.isdigit() and int(value) in ids:
                found[int(value)] = row
            if len(found) == len(ids):
                break
    for concept_id in ids:
        row = found.get(concept_id)
        if row is None or row["standard_concept"] != "S" or row["invalid_reason"] or row["domain_id"] != "Condition":
            raise ValueError(f"Target {concept_id} is not a current standard Condition concept")
    return {key: {"concept_name": row["concept_name"], "vocabulary_id": row["vocabulary_id"]}
            for key, row in found.items()}


def apply(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output directory is not empty: {args.output}; choose a fresh directory")
    approved = read_review(args.review)
    counts, dated, people, _ = inventory(args.input)
    unknown = set(approved) - set(counts)
    if unknown:
        raise ValueError(f"Approved codes absent from source: {sorted(unknown)[:10]}")
    concepts = validate_targets(args.vocabulary, approved)
    mapper = load_mapper()
    versions = mapper.verify_vocabulary(args.vocabulary)
    tables, base_report = mapper.make_tables(args.input, e11_only=False)
    person_count = len(tables["person"])
    if person_count != people:
        raise ValueError("Person count changed during mapping")
    mapped = collections.Counter()
    conditions = []
    for row in tables["condition_occurrence"]:
        code = row[5].strip().upper()
        entry = approved.get(code)
        if entry:
            mapped[code] += 1
            for target in entry["targets"]:
                conditions.append([len(conditions) + 1, row[1], target, row[3], row[4], row[5]])
        else:
            row[0] = len(conditions) + 1
            row[2] = 0
            conditions.append(row)
    total = len(tables["condition_occurrence"])
    tables["condition_occurrence"] = conditions
    mapped_records = sum(mapped.values())
    report = {
        "source": str(args.input), "review": str(args.review),
        "vocabulary_versions": versions,
        "table_rows": {name: len(rows) for name, rows in tables.items()},
        "diagnosis_date_or_value_issues": {
            key: value for key, value in base_report["counts"].items()
            if key != "e11_rollup_records"
        },
        "dated_diagnosis_records": total,
        "mapped_diagnosis_records": mapped_records,
        "mapped_condition_rows": sum(len(approved[code]["targets"]) * count for code, count in mapped.items()),
        "mapped_diagnosis_record_fraction": mapped_records / total if total else None,
        "distinct_dated_diagnosis_codes": len({row[5].strip().upper() for row in tables["condition_occurrence"]}),
        "mapped_distinct_diagnosis_codes": len(mapped),
        "approved_mappings": [
            {"source_code": code, "target_concept_ids": item["targets"],
             "target_names": [concepts[target]["concept_name"] for target in item["targets"]],
             "mapping_kind": item["kind"], "evidence": item["evidence"],
             "mapped_records": mapped[code]}
            for code, item in sorted(approved.items())
        ],
        "notes": [
            "Only explicitly approved source codes receive standard concepts; multiple approved targets create multiple condition rows.",
            "Record coverage measures technical annotation, not clinical mapping accuracy.",
            "Other pilot assumptions, including type concept 0 and technical observation periods, remain in force.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (args.output / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(mapper.TABLES[name])
            writer.writerows(rows)
    (args.output / "mapping_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "apply"):
        sub = subs.add_parser(command)
        sub.add_argument("--input", type=Path, required=True)
        sub.add_argument("--vocabulary", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        if command == "apply":
            sub.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        (inspect if args.command == "inspect" else apply)(args)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
