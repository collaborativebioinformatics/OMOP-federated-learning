# Acknowledgement:
# Anthropic. (2026). Claude (Version 3.5 Sonnet). 
# https://anthropic.com
# Used for: Code optimization and error handling.

#!/usr/bin/env python
"""
qc_report.py — lightweight OMOP QC pass, meant to run as step 5 after
run_mapping.py and validate_contract.py.

Covers the four checks from the QC doc using only DuckDB + pandas
(no Achilles/DQD/White Rabbit/Usagi — those need R/Java; this
reimplements the same checks against the contract tables directly):

  1. Missingness         (DQD "Completeness" equivalent)
  2. All tables present  (Achilles row-count equivalent)
  3. Date format/logic   (DQD "Plausibility" equivalent)
  4. Mapping rate         (Achilles Heel concept_id=0 equivalent)

Assumes standard OMOP CDM 5.3/5.4 column names. If your contract.yaml
renames columns, adjust the *_SUFFIXES / REQUIRED_FIELDS constants below.

Usage:
    python qc_report.py --output omop_skill/output/site_a \
        --reference synthea_cohorts/cohort_2/data/omop/site_a \
        --out omop_skill/output/site_a/qc_report.md

    # also dump machine-readable results for subproject 2
    python qc_report.py --output omop_skill/output/site_a --json qc.json
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import duckdb
import pandas as pd

# ---- conventions -----------------------------------------------------

# Columns that should basically never be null, per table, if present.
REQUIRED_FIELDS = {
    "person": ["person_id", "gender_concept_id", "year_of_birth"],
    "visit_occurrence": ["visit_occurrence_id", "person_id", "visit_start_date"],
    "condition_occurrence": ["condition_occurrence_id", "person_id", "condition_concept_id", "condition_start_date"],
    "drug_exposure": ["drug_exposure_id", "person_id", "drug_concept_id", "drug_exposure_start_date"],
    "measurement": ["measurement_id", "person_id", "measurement_concept_id", "measurement_date"],
    "observation_period": ["person_id", "observation_period_start_date", "observation_period_end_date"],
}

# start/end date pairs to check start <= end, wherever both columns exist.
START_END_SUFFIXES = [
    ("_start_date", "_end_date"),
    ("_start_datetime", "_end_datetime"),
]

MISSING_THRESHOLD_DEFAULT = 5.0   # percent
MAPPING_THRESHOLD_DEFAULT = 20.0  # percent unmapped (concept_id = 0) flagged


# ---- IO ----------------------------------------------------------------

def discover_tables(directory):
    """Find table files (csv, csv.gz, parquet) in a directory, keyed by table name."""
    tables = {}
    if not directory or not os.path.isdir(directory):
        return tables
    for pattern in ("*.csv", "*.csv.gz", "*.parquet"):
        for path in glob.glob(os.path.join(directory, pattern)):
            name = os.path.basename(path)
            for ext in (".csv.gz", ".csv", ".parquet"):
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            tables[name] = path
    return tables


def load(path):
    """Load a table file into a DuckDB relation via a fresh in-memory connection."""
    con = duckdb.connect()
    if path.endswith(".parquet"):
        rel = con.read_parquet(path)
    else:
        rel = con.read_csv(path)
    return con, rel


# ---- checks --------------------------------------------------------------

def check_tables_present(tables, expected_tables):
    rows = []
    for t in expected_tables:
        if t not in tables:
            rows.append({"table": t, "status": "MISSING", "row_count": 0})
            continue
        con, rel = load(tables[t])
        con.register("rel", rel)
        n = con.execute("SELECT COUNT(*) FROM rel").fetchone()
        rows.append({"table": t, "status": "OK" if n[0] > 0 else "EMPTY", "row_count": n[0]})
        con.close()
    # also flag any tables present on disk but not in the expected contract list
    for t in tables:
        if t not in expected_tables:
            rows.append({"table": t, "status": "UNEXPECTED (not in contract list)", "row_count": None})
    return rows


def check_missingness(tables, threshold):
    rows = []
    for table_name, path in tables.items():
        con, rel = load(path)
        columns = rel.columns
        con.register("rel", rel)
        total = con.execute("SELECT COUNT(*) FROM rel").fetchone()[0]
        if total == 0:
            con.close()
            continue
        required = REQUIRED_FIELDS.get(table_name, [])
        cols_to_check = [c for c in columns if c in required] or columns
        for col in cols_to_check:
            n_null = con.execute(f'SELECT COUNT(*) FROM rel WHERE "{col}" IS NULL').fetchone()[0]
            pct = round(100.0 * n_null / total, 2)
            flagged = pct > threshold and col in required
            rows.append({
                "table": table_name, "column": col,
                "pct_missing": pct, "required_field": col in required,
                "flag": "FLAG" if flagged else "",
            })
        con.close()
    return rows


def check_date_logic(tables):
    rows = []
    for table_name, path in tables.items():
        con, rel = load(path)
        columns = rel.columns
        con.register("rel", rel)

        # birth_datetime / year_of_birth not in the future
        if "year_of_birth" in columns:
            n_future = con.execute(
                f"SELECT COUNT(*) FROM rel WHERE year_of_birth > {datetime.now().year}"
            ).fetchone()[0]
            rows.append({"table": table_name, "check": "year_of_birth not in future",
                         "violations": n_future, "flag": "FLAG" if n_future else ""})

        # start <= end pairs
        for start_suf, end_suf in START_END_SUFFIXES:
            starts = [c for c in columns if c.endswith(start_suf)]
            for start_col in starts:
                prefix = start_col[: -len(start_suf)]
                end_col = prefix + end_suf
                if end_col in columns:
                    n_bad = con.execute(
                        f'SELECT COUNT(*) FROM rel WHERE "{start_col}" IS NOT NULL '
                        f'AND "{end_col}" IS NOT NULL AND "{start_col}" > "{end_col}"'
                    ).fetchone()[0]
                    rows.append({"table": table_name, "check": f"{start_col} <= {end_col}",
                                 "violations": n_bad, "flag": "FLAG" if n_bad else ""})
        con.close()
    return rows


def check_mapping_rate(tables, threshold):
    rows = []
    for table_name, path in tables.items():
        con, rel = load(path)
        columns = rel.columns
        con.register("rel", rel)
        total = con.execute("SELECT COUNT(*) FROM rel").fetchone()[0]
        if total == 0:
            con.close()
            continue
        concept_cols = [c for c in columns if c.endswith("_concept_id") and not c.startswith("gender")
                         and not c.startswith("race") and not c.startswith("ethnicity")]
        for col in concept_cols:
            n_unmapped = con.execute(f'SELECT COUNT(*) FROM rel WHERE "{col}" = 0').fetchone()[0]
            pct = round(100.0 * n_unmapped / total, 2)
            rows.append({"table": table_name, "column": col, "pct_unmapped": pct,
                         "flag": "FLAG" if pct > threshold else ""})
        con.close()
    return rows


def compare_row_counts(tables, ref_tables):
    rows = []
    all_names = sorted(set(tables) | set(ref_tables))
    for name in all_names:
        our_n = None
        ref_n = None
        if name in tables:
            con, rel = load(tables[name])
            con.register("rel", rel)
            our_n = con.execute("SELECT COUNT(*) FROM rel").fetchone()[0]
            con.close()
        if name in ref_tables:
            con, rel = load(ref_tables[name])
            con.register("rel", rel)
            ref_n = con.execute("SELECT COUNT(*) FROM rel").fetchone()[0]
            con.close()
        match = (our_n == ref_n)
        rows.append({"table": name, "our_rows": our_n, "reference_rows": ref_n,
                     "match": match, "flag": "" if match else "FLAG"})
    return rows


# ---- report writing --------------------------------------------------------

def to_md_table(rows):
    if not rows:
        return "_none_\n"
    headers = list(rows[0].keys())
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |")
    return "\n".join(lines) + "\n"


def write_markdown(out_path, site, present, missing, dates, mapping, row_compare):
    def n_flags(rows):
        return sum(1 for r in rows if r.get("flag"))

    with open(out_path, "w") as f:
        f.write(f"# OMOP QC Report — {site}\n\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write("## Summary\n\n")
        f.write(f"- Tables present: {n_flags(present)} issue(s)\n")
        f.write(f"- Missingness: {n_flags(missing)} field(s) over threshold\n")
        f.write(f"- Date logic: {n_flags(dates)} check(s) with violations\n")
        f.write(f"- Mapping rate: {n_flags(mapping)} column(s) over threshold\n")
        if row_compare:
            f.write(f"- Row count vs reference: {n_flags(row_compare)} mismatch(es)\n")
        f.write("\n## 1. Tables present\n\n" + to_md_table(present))
        f.write("\n## 2. Missingness\n\n" + to_md_table(missing))
        f.write("\n## 3. Date format / logic\n\n" + to_md_table(dates))
        f.write("\n## 4. Mapping rate (concept_id = 0)\n\n" + to_md_table(mapping))
        if row_compare:
            f.write("\n## 5. Row counts vs reference\n\n" + to_md_table(row_compare))
        f.write("\nNot everything flagged needs to be fixed — some reflect genuine "
                 "source data limitations. Review flagged rows before acting.\n")


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="QC report for converted OMOP contract tables")
    ap.add_argument("--output", required=True, help="Directory of converted OMOP contract tables")
    ap.add_argument("--reference", default=None, help="Optional reference OMOP dir (e.g. Synthea) to compare row counts")
    ap.add_argument("--tables", nargs="*", default=list(REQUIRED_FIELDS.keys()),
                     help="Expected contract table names (default: standard OMOP set in this script)")
    ap.add_argument("--missing-threshold", type=float, default=MISSING_THRESHOLD_DEFAULT)
    ap.add_argument("--mapping-threshold", type=float, default=MAPPING_THRESHOLD_DEFAULT)
    ap.add_argument("--out", default=None, help="Markdown report path (default: <output>/qc_report.md)")
    ap.add_argument("--json", default=None, help="Optional JSON report path")
    args = ap.parse_args()

    tables = discover_tables(args.output)
    if not tables:
        print(f"No table files found in {args.output}", file=sys.stderr)
        sys.exit(1)

    present = check_tables_present(tables, args.tables)
    missing = check_missingness(tables, args.missing_threshold)
    dates = check_date_logic(tables)
    mapping = check_mapping_rate(tables, args.mapping_threshold)

    row_compare = []
    if args.reference:
        ref_tables = discover_tables(args.reference)
        if ref_tables:
            row_compare = compare_row_counts(tables, ref_tables)
        else:
            print(f"Warning: no table files found in reference dir {args.reference}", file=sys.stderr)

    site = os.path.basename(os.path.normpath(args.output))
    out_path = args.out or os.path.join(args.output, "qc_report.md")
    write_markdown(out_path, site, present, missing, dates, mapping, row_compare)
    print(f"Wrote {out_path}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "site": site,
                "tables_present": present,
                "missingness": missing,
                "date_logic": dates,
                "mapping_rate": mapping,
                "row_count_vs_reference": row_compare,
            }, f, indent=2, default=str)
        print(f"Wrote {args.json}")

    any_flag = any(r.get("flag") for r in present + missing + dates + mapping + row_compare)
    sys.exit(1 if any_flag else 0)


if __name__ == "__main__":
    main()
