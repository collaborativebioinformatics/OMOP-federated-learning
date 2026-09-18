#!/usr/bin/env python3
"""Summarize the eight requested variables by disease or assay name."""

import argparse
import collections
import csv
import json
import re
from pathlib import Path


DISEASES = {"AD": ("G30", "F00"), "PD": ("G20",), "T2D": ("E11",)}
ASSAYS = {
    "Blood glucose": "30740",
    "HDL cholesterol": "30760",
    "LDL direct": "30780",
    "Triglycerides": "30870",
    "Total cholesterol": "30690",
}
COLUMN = re.compile(r"^(\d+)-(\d+)\.(\d+)$")


def summarize(source, run):
    review = list(csv.DictReader((run / "mapping_review.csv").open(encoding="utf-8-sig", newline="")))
    report_path = run / "omop" / "mapping_report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    grouped = {name: {"participants": set(), "source_records": 0, "dated_records": 0}
               for name in (*DISEASES, *ASSAYS)}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames or []
        columns = collections.defaultdict(list)
        for name in header:
            match = COLUMN.fullmatch(name)
            if match:
                columns[match.group(1)].append((name, match.group(2), match.group(3)))
        for row in reader:
            eid = row["EID"].strip()
            for name, prefixes in DISEASES.items():
                for column, instance, array in columns["41270"]:
                    code = row[column].strip().upper().replace(".", "")
                    if not code.startswith(prefixes):
                        continue
                    item = grouped[name]
                    item["participants"].add(eid)
                    item["source_records"] += 1
                    if row.get(f"41280-{instance}.{array}", "").strip():
                        item["dated_records"] += 1
            for name, field_id in ASSAYS.items():
                for column, instance, _ in columns[field_id]:
                    if not row[column].strip():
                        continue
                    item = grouped[name]
                    item["participants"].add(eid)
                    item["source_records"] += 1
                    if row.get(f"53-{instance}.0", "").strip():
                        item["dated_records"] += 1

    mapped_by_code = collections.Counter()
    mapped_by_field = {}
    if report:
        mapped_by_code.update({entry["source_code"]: entry["records"]
                               for entry in report["mapped_source_events_by_key"]
                               if entry["field_id"] == "41270"})
        mapped_by_field = report["mapped_source_events_by_field"]
    rows = []
    for name, prefixes in DISEASES.items():
        item = grouped[name]
        matches = [entry for entry in review if entry["source_code"].startswith(prefixes)]
        rule = " or ".join(f"{prefix}*" for prefix in prefixes)
        rows.append({"variable": name, "source_field": "41270", "source_rule": f"ICD10 {rule}",
                     "participants": len(item["participants"]),
                     "source_records": item["source_records"],
                     "dated_records": item["dated_records"],
                     "candidate_records": sum(int(entry["dated_records"]) for entry in matches
                                              if json.loads(entry["candidate_targets_json"])),
                     "mapped_records": sum(mapped_by_code[entry["source_code"]] for entry in matches)
                                       if report else None,
                     "reviewed_codes": sum(entry["decision"] == "approved" for entry in matches),
                     "source_codes": len(matches)})
    for name, field_id in ASSAYS.items():
        item = grouped[name]
        rows.append({"variable": name, "source_field": field_id,
                     "source_rule": f"UKB field {field_id}, mmol/L",
                     "participants": len(item["participants"]),
                     "source_records": item["source_records"],
                     "dated_records": item["dated_records"],
                     "candidate_records": None,
                     "mapped_records": mapped_by_field.get(field_id) if report else None,
                     "reviewed_codes": None, "source_codes": None})
    note = ("UKB synthetic proxy; mapped means a reviewed standard concept was written, "
            "not clinical validation." if report else
            "UKB synthetic proxy; diagnosis candidates are not approved mappings.")
    return {"source": str(source), "run": str(run), "applied": report is not None,
            "note": note,
            "variables": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.source, args.run)
    (args.run / "analysis_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    with (args.run / "analysis_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result["variables"][0]))
        writer.writeheader()
        writer.writerows(result["variables"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
