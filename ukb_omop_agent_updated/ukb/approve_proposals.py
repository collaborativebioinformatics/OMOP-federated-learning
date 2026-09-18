#!/usr/bin/env python3
"""Record an explicit user approval of the saved disease target proposal."""

import argparse
import csv
import hashlib
import json
from pathlib import Path


def approve(run, evidence):
    proposal_path = run / "proposed_disease_targets.csv"
    review_path = run / "mapping_review.csv"
    with proposal_path.open(encoding="utf-8", newline="") as source:
        proposals = {(row["source_vocabulary"], row["source_code"]): row
                     for row in csv.DictReader(source)}
    if len(proposals) != 19:
        raise ValueError(f"Expected 19 disease proposals, found {len(proposals)}")
    with review_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames
        rows = list(reader)
    found = set()
    proposal_hash = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    for row in rows:
        key = (row["source_vocabulary"], row["source_code"])
        if key not in proposals:
            continue
        proposal = proposals[key]
        concept_id = int(proposal["proposed_target_id"])
        candidates = json.loads(row["candidate_targets_json"])
        if not any(int(item["concept_id"]) == concept_id and item["domain"] == "Condition"
                   for item in candidates):
            raise ValueError(f"Proposed target is not an Athena Condition candidate: {key}")
        if row["decision"] != "needs_review" or row["approved_target_ids"]:
            raise ValueError(f"Review row has already been changed: {key}")
        if row["dated_records"] != proposal["dated_records"]:
            raise ValueError(f"Source count changed for {key}")
        row["decision"] = "approved"
        row["approved_target_ids"] = str(concept_id)
        row["mapping_kind"] = "vocabulary_maps_to"
        row["evidence"] = (f"{evidence}; Athena ICD10 Maps to {concept_id}; "
                           f"proposal_sha256={proposal_hash}")
        found.add(key)
    if found != set(proposals):
        raise ValueError(f"Missing proposal rows: {set(proposals) - found}")
    temporary = review_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(review_path)
    return len(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--evidence", required=True, help="Explicit user approval to record")
    parser.add_argument("--approve-proposed", action="store_true", required=True)
    args = parser.parse_args()
    print(f"Recorded {approve(args.run, args.evidence)} reviewed mappings")


if __name__ == "__main__":
    main()
