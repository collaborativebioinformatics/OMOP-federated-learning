#!/usr/bin/env python3
"""Write a reviewable disease-focused target proposal; never approve mappings."""

import argparse
import csv
import json
from pathlib import Path


def propose(review_path, output_path):
    rows = []
    with review_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            code = row["source_code"]
            candidates = json.loads(row["candidate_targets_json"])
            if code.startswith("E11"):
                disease, preferred = "T2D", 201826
                detail = "Broad T2D rollup; complication detail is not represented by this target"
            elif code.startswith(("G30", "F00")):
                disease, preferred = "AD", None
                detail = "Uses the sole Athena target; early/late onset detail is retained where available"
            elif code.startswith("G20"):
                disease, preferred = "PD", None
                detail = "Uses the sole Athena target"
            else:
                continue
            if preferred is None:
                if len(candidates) != 1:
                    raise ValueError(f"Expected one Athena target for {code}, found {len(candidates)}")
                target = candidates[0]
            else:
                target = next((item for item in candidates
                               if int(item["concept_id"]) == preferred), None)
                if target is None:
                    raise ValueError(f"Athena target {preferred} unavailable for {code}")
            rows.append({"disease": disease, "source_vocabulary": row["source_vocabulary"],
                         "source_code": code, "dated_records": row["dated_records"],
                         "proposed_target_id": target["concept_id"],
                         "proposed_target_name": target["name"],
                         "domain": target["domain"], "review_note": detail,
                         "decision": "needs_review"})
    with output_path.open("w", encoding="utf-8", newline="") as target_file:
        writer = csv.DictWriter(target_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    output = args.run / "proposed_disease_targets.csv"
    rows = propose(args.run / "mapping_review.csv", output)
    print(f"Wrote {len(rows)} unapproved proposals to {output}")


if __name__ == "__main__":
    main()
