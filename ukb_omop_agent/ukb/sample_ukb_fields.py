#!/usr/bin/env python3
"""Stream a small matching-row sample from official UKB synthetic TSVs.

Unlike download_ukb_fields.py, this stops after --rows data records per file.
It verifies that every file supplies the same EIDs in the same order.
Partial files cannot be checked against UKB's whole-file MD5 checksums.
"""

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path

from download_ukb_fields import DEFAULT_FIELDS, MD5_URL, PAGE_URL, discover


def existing_sample(destination, row_count, expected_eids):
    eids = []
    with destination.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, delimiter="\t")
        header = next(reader, None)
        if not header or header[0].strip().lower() != "eid":
            raise ValueError(f"Unexpected header in existing sample: {destination}")
        for row in reader:
            if not row or len(row) != len(header) or not row[0].strip():
                raise ValueError(f"Incomplete row in existing sample: {destination}")
            eids.append(row[0].strip())
            if len(eids) > row_count:
                break
    if len(eids) != row_count or len(set(eids)) != row_count:
        raise ValueError(
            f"Existing sample {destination} does not contain exactly {row_count} unique EIDs"
        )
    if expected_eids is not None and eids != expected_eids:
        raise ValueError(f"EID order differs in existing sample: {destination}")
    print(f"Reusing {len(eids)} rows: {destination}", flush=True)
    return eids


def sample_file(item, output_dir, row_count, expected_eids=None):
    destination = output_dir / item["filename"]
    if destination.exists():
        return existing_sample(destination, row_count, expected_eids)
    temporary = destination.with_name(destination.name + ".part")
    eids = []
    request = urllib.request.Request(
        item["url"],
        headers={"User-Agent": "ukb-omop-pilot/1.0", "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text_source = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
            reader = csv.reader(text_source, delimiter="\t")
            header = next(reader, None)
            if not header or header[0].strip().lower() != "eid":
                raise ValueError(f"Unexpected header in {item['filename']}")
            with temporary.open("w", encoding="utf-8", newline="") as target:
                writer = csv.writer(target, delimiter="\t")
                writer.writerow(header)
                for row in reader:
                    if not row:
                        continue
                    eid = row[0].strip()
                    if not eid:
                        raise ValueError(f"Empty EID in {item['filename']}")
                    if expected_eids is not None:
                        index = len(eids)
                        if index >= len(expected_eids) or eid != expected_eids[index]:
                            raise ValueError(
                                f"EID order differs in {item['filename']} at row {index + 1}: {eid}"
                            )
                    writer.writerow(row)
                    eids.append(eid)
                    if len(eids) == row_count:
                        break
        if len(eids) != row_count:
            raise ValueError(f"Only {len(eids)} rows found in {item['filename']}")
        temporary.replace(destination)
        print(f"Sampled {len(eids)} rows: {destination}", flush=True)
        return eids
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=Path("data/ukb_sampled"))
    parser.add_argument("--page-url", default=PAGE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--md5-url", default=MD5_URL, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("--rows must be positive")

    files = discover(args.page_url, args.md5_url, set(DEFAULT_FIELDS))
    args.output.mkdir(parents=True, exist_ok=True)
    expected_eids = None
    for item in files:
        expected_eids = sample_file(item, args.output, args.rows, expected_eids)
    manifest = {
        "selection": "first rows in each official UKB field-group file",
        "rows_per_file": args.rows,
        "field_ids": list(DEFAULT_FIELDS),
        "files": [item["filename"] for item in files],
        "whole_file_md5_verified": False,
        "eid_order_verified_across_files": True,
    }
    (args.output / "sample_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"All samples have the same {args.rows} EIDs.")


if __name__ == "__main__":
    main()
