#!/usr/bin/env python3
"""Reuse the UKB sampler to add the five blood assays to an aligned sample."""

import argparse
import csv
from pathlib import Path

from sample_ukb_fields import sample_file


BIOMARKER_FILE = {
    "filename": "real_fields2.tsv",
    "url": "https://biobank.ndph.ox.ac.uk/synthetic_dataset/tabular/real_fields2.tsv",
}


def reference_eids(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, delimiter="\t")
        header = next(reader, None)
        if not header or header[0].strip().lower() != "eid":
            raise ValueError(f"Expected an EID column in {path}")
        eids = [row[0].strip() for row in reader if row]
    if not eids or len(eids) != len(set(eids)):
        raise ValueError(f"Reference sample has empty or duplicate EIDs: {path}")
    return eids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, required=True)
    args = parser.parse_args()
    expected = reference_eids(args.sample_dir / "integer_no_arrays.tsv")
    sample_file(BIOMARKER_FILE, args.sample_dir, len(expected), expected)


if __name__ == "__main__":
    main()
