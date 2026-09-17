#!/usr/bin/env python3
"""List exactly which selected source codes mapped and why others did not."""

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path


SUPPORTED_DOMAINS = {"Condition", "Measurement", "Observation", "Procedure"}
COLUMNS = [
    "field_id", "source_vocabulary", "source_code", "source_records", "dated_records",
    "undated_records", "athena_source_concept_ids", "candidate_target_ids", "candidate_domains",
    "candidate_value_ids", "approved_target_ids", "approved_value_concept_id",
    "status", "reason", "mapped_source_records", "unmapped_dated_records",
    "omop_rows_written", "evidence",
]
FIELD_COLUMNS = ["field_id", "source_events", "dated_events", "mapped_source_events", "omop_rows"]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def classify(review_row, mapped, dated):
    decision = review_row["decision"].strip().lower()
    candidates = json.loads(review_row["candidate_targets_json"])
    if mapped == dated and mapped > 0:
        return "mapped", "Approved target written for every dated source record"
    if mapped > 0:
        return "partially_mapped", "Some dated source records were excluded during transformation"
    if dated == 0:
        return "no_dated_records", "Source code has no valid paired date"
    if decision == "approved":
        return "approved_not_written", "Approved source code has dated records but no standard OMOP row was written"
    if decision == "unmapped":
        return "explicitly_unmapped", "Reviewer marked this source code unmapped"
    if not review_row["source_concept_ids"].strip():
        return "no_source_concept", "No exact code match in the selected Athena source vocabulary"
    if not candidates:
        return "no_standard_candidate", "Source concept found, but no active standard Maps to target"
    if not any(target["domain"] in SUPPORTED_DOMAINS for target in candidates):
        return "unsupported_domain_candidate", "Athena targets need an output domain not yet supported by this agent"
    return "awaiting_review", "Athena candidate exists but has not been approved"


def build_inventory(run_dir, omop_dir, review_path):
    preflight = json.loads((run_dir / "preflight.json").read_text(encoding="utf-8"))
    report = json.loads((omop_dir / "mapping_report.json").read_text(encoding="utf-8"))
    qc = json.loads((omop_dir / "qc.json").read_text(encoding="utf-8"))
    for key in ("source_sha256", "spec_sha256", "vocabulary_release", "diagnosis_selection"):
        if preflight.get(key) != report.get(key):
            raise ValueError(f"Preflight and OMOP report disagree on {key}")
    if report.get("review_sha256") != digest(review_path):
        raise ValueError("Review file differs from the one used to create this OMOP output")
    mapped_by_key = {
        (item["field_id"], item["source_vocabulary"], item["source_code"]): item["records"]
        for item in report.get("mapped_source_events_by_key", [])
    }
    rows_by_key = collections.Counter()
    for item in report.get("mapped_rows_by_key_domain", []):
        rows_by_key[(item["field_id"], item["source_vocabulary"], item["source_code"])] += item["rows"]
    entries = []
    with review_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = (row["field_id"], row["source_vocabulary"], row["source_code"].upper())
            total, dated = int(row["records"]), int(row["dated_records"])
            mapped = mapped_by_key.get(key, 0)
            if mapped > dated or dated > total:
                raise ValueError(f"Mapping counts are inconsistent for {key}")
            candidates = json.loads(row["candidate_targets_json"])
            status, reason = classify(row, mapped, dated)
            entries.append({
                "field_id": key[0], "source_vocabulary": key[1], "source_code": key[2],
                "source_records": total, "dated_records": dated, "undated_records": total - dated,
                "athena_source_concept_ids": row["source_concept_ids"],
                "candidate_target_ids": ";".join(str(target["concept_id"]) for target in candidates),
                "candidate_domains": ";".join(sorted({target["domain"] for target in candidates})),
                "candidate_value_ids": row["candidate_value_ids"],
                "approved_target_ids": row["approved_target_ids"],
                "approved_value_concept_id": row["approved_value_concept_id"],
                "status": status, "reason": reason,
                "mapped_source_records": mapped, "unmapped_dated_records": dated - mapped,
                "omop_rows_written": rows_by_key.get(key, 0), "evidence": row["evidence"],
            })
    fields = []
    for field, count in sorted(report.get("source_events_by_field", {}).items()):
        rows = sum(item["rows"] for item in report.get("output_rows_by_field_domain", [])
                   if item["field_id"] == field)
        fields.append({"field_id": field, "source_events": count,
                       "dated_events": report.get("dated_events_by_field", {}).get(field, 0),
                       "mapped_source_events": report.get("mapped_source_events_by_field", {}).get(field, 0),
                       "omop_rows": rows})
    status_codes = collections.Counter(item["status"] for item in entries)
    status_records = collections.Counter()
    for item in entries:
        if item["unmapped_dated_records"]:
            status_records[item["status"]] += item["unmapped_dated_records"]
    summary = {
        "diagnosis_selection": preflight["diagnosis_selection"],
        "participants": preflight["people"],
        "selected_source_records": sum(item["source_records"] for item in entries),
        "dated_source_records": sum(item["dated_records"] for item in entries),
        "mapped_source_records": sum(item["mapped_source_records"] for item in entries),
        "unmapped_dated_records": sum(item["unmapped_dated_records"] for item in entries),
        "undated_source_records": sum(item["undated_records"] for item in entries),
        "status_code_counts": dict(status_codes),
        "status_unmapped_record_counts": dict(status_records),
        "demonstration": any("demonstration" in item["evidence"].lower() for item in entries
                             if item["mapped_source_records"]),
        "field_summary": fields,
        "structural_qc_pass": bool(qc.get("pass")),
        "note": "Mapped means a reviewed standard concept was written; it does not prove clinical correctness.",
    }
    return entries, summary


def export(run_dir, omop_dir, review_path, output_dir):
    entries, summary = build_inventory(run_dir, omop_dir, review_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    subsets = {
        "mapping_inventory.csv": entries,
        "mapped_codes.csv": [item for item in entries if item["mapped_source_records"]],
        "missing_codes.csv": [item for item in entries if item["unmapped_dated_records"] or item["undated_records"]],
    }
    for filename, rows in subsets.items():
        with (output_dir / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    with (output_dir / "field_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELD_COLUMNS)
        writer.writeheader()
        writer.writerows(summary["field_summary"])
    (output_dir / "mapping_inventory_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--omop", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    review = args.review or args.run / "mapping_review.csv"
    output = args.output or args.omop
    try:
        print(json.dumps(export(args.run, args.omop, review, output), indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
