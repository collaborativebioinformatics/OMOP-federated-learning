#!/usr/bin/env python3
"""
omop_qc.py
==========

Lightweight OMOP CDM QC pass for four flat CSV extracts per site/cohort:

    person.csv
    observation_period.csv
    condition_occurrence.csv
    measurement.csv

This mirrors the four checks in the QC checklist (table presence,
missingness, date validity/logic, and mapping rate to standard OMOP
concepts) but is adapted to work directly on CSVs with pandas instead
of a live OMOP CDM database.

Supports either a single site (one directory holding the four CSVs) or
multiple sites/cohorts (a parent directory containing one subdirectory
per site, each with its own four CSVs). Each site gets its own report
section, followed by a cross-site summary.

Usage:
    # Single site: --dir points straight at the folder with the 4 CSVs
    python omop_qc.py --dir /path/to/site_csvs --out qc_report.txt

    # Multiple sites: --dir points at a parent folder whose
    # subdirectories are each a site (site_a/, site_b/, ...)
    python omop_qc.py --dir /path/to/all_sites --out qc_report.txt

    # Or name sites explicitly (repeatable), mixed with --dir if needed
    python omop_qc.py --site siteA=/path/to/siteA --site siteB=/path/to/siteB

    # Single-table overrides (single site only)
    python omop_qc.py --person person.csv --measurement measurement.csv \\
        --observation_period observation_period.csv \\
        --condition_occurrence condition_occurrence.csv
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

TODAY = pd.Timestamp(datetime.now().date())

# ---------------------------------------------------------------------------
# Configuration: expected columns, required fields, date columns, and
# concept columns for each OMOP table. Edit these if your schema changes.
# ---------------------------------------------------------------------------

# Candidate filenames per table (first match wins). Includes a couple of
# common misspellings/variants people actually ship (e.g. missing the
# second "r" in condition_occurrence).
TABLE_FILE_CANDIDATES = {
    "person": ["person.csv"],
    "observation_period": ["observation_period.csv"],
    "condition_occurrence": [
        "condition_occurrence.csv",
        "condition_occurence.csv",
        "condition_occurrences.csv",
    ],
    "measurement": ["measurement.csv"],
}

EXPECTED_SCHEMAS = {
    "person": [
        "person_id", "gender_concept_id", "year_of_birth",
        "race_concept_id", "ethnicity_concept_id", "person_source_value",
    ],
    "observation_period": [
        "observation_period_id", "person_id", "observation_period_start_date",
        "observation_period_end_date", "period_type_concept_id",
    ],
    "condition_occurrence": [
        "condition_occurrence_id", "person_id", "condition_concept_id",
        "condition_start_date", "condition_type_concept_id",
        "condition_source_value",
    ],
    "measurement": [
        "measurement_id", "person_id", "measurement_concept_id",
        "measurement_date", "measurement_type_concept_id",
        "value_as_number", "unit_concept_id", "measurement_source_value",
    ],
}

# Fields that should essentially never be null (ETL failure if they are).
REQUIRED_NOT_NULL = {
    "person": ["person_id", "year_of_birth"],
    "observation_period": [
        "observation_period_id", "person_id",
        "observation_period_start_date", "observation_period_end_date",
    ],
    "condition_occurrence": [
        "condition_occurrence_id", "person_id", "condition_concept_id",
        "condition_start_date",
    ],
    "measurement": [
        "measurement_id", "person_id", "measurement_concept_id",
        "measurement_date",
    ],
}

# Date columns per table, used for both parse-validity and logic checks.
DATE_COLUMNS = {
    "observation_period": [
        "observation_period_start_date", "observation_period_end_date",
    ],
    "condition_occurrence": ["condition_start_date"],
    "measurement": ["measurement_date"],
}

# concept_id columns used to compute the OMOP mapping rate, and the
# corresponding *_source_value column (if any) used to break down where
# mapping is weak.
CONCEPT_COLUMNS = {
    "condition_occurrence": "condition_concept_id",
    "measurement": "measurement_concept_id",
}
TABLE_SOURCE_VALUE_COL = {
    "condition_occurrence": "condition_source_value",
    "measurement": "measurement_source_value",
}
# Demographic concept columns on person -- checked for mapping rate too,
# but with no source_value breakdown.
PERSON_CONCEPT_COLUMNS = ["gender_concept_id", "race_concept_id", "ethnicity_concept_id"]

MISSINGNESS_THRESHOLD = 0.05  # flag columns with >5% nulls
MAPPING_RATE_THRESHOLD = 0.05  # flag if >5% of rows are unmapped (concept_id = 0 / null)


# ---------------------------------------------------------------------------
# Report scaffolding
# ---------------------------------------------------------------------------

@dataclass
class QCReport:
    sections: list = field(default_factory=list)

    def add(self, title, lines):
        self.sections.append((title, lines))

    def render(self, header_lines=None):
        out = []
        for line in (header_lines or []):
            out.append(line)
        for title, lines in self.sections:
            out.append("")
            out.append(f"## {title}")
            out.append("-" * 78)
            if not lines:
                out.append("  (nothing to report)")
            for line in lines:
                out.append(f"  {line}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# 0. CSV loading (delimiter-flexible: comma, tab, semicolon, pipe)
# ---------------------------------------------------------------------------

def read_csv_flexible(path):
    """Read a CSV whose delimiter might be comma, tab, semicolon or pipe."""
    try:
        df = pd.read_csv(path, dtype=str, sep=None, engine="python")
    except Exception:
        df = None

    if df is None or df.shape[1] <= 1:
        for sep in ["\t", ",", ";", "|"]:
            try:
                candidate = pd.read_csv(path, dtype=str, sep=sep, engine="python")
            except Exception:
                continue
            if candidate.shape[1] > 1:
                df = candidate
                break
        if df is None:
            df = pd.read_csv(path, dtype=str)  # last resort, let it raise if truly broken

    df.columns = [c.strip() for c in df.columns]
    return df


def resolve_table_paths(site_dir):
    """Case-insensitively match TABLE_FILE_CANDIDATES against files in site_dir."""
    try:
        entries = {f.lower(): f for f in os.listdir(site_dir)}
    except OSError:
        entries = {}

    paths = {}
    for table, candidates in TABLE_FILE_CANDIDATES.items():
        found = None
        for cand in candidates:
            if cand.lower() in entries:
                found = os.path.join(site_dir, entries[cand.lower()])
                break
        paths[table] = found
    return paths


def load_tables(paths):
    """paths: dict of table_name -> csv path (or None). Returns dict of DataFrames."""
    tables = {}
    for name, path in paths.items():
        if path is None:
            continue
        if not os.path.isfile(path):
            print(f"WARNING: {name} file not found at {path}, skipping.", file=sys.stderr)
            continue
        df = read_csv_flexible(path)
        tables[name] = df
    return tables


# ---------------------------------------------------------------------------
# 1. Confirm all expected tables/columns are present
# ---------------------------------------------------------------------------

def check_tables_present(tables, report):
    lines = []
    for name, expected_cols in EXPECTED_SCHEMAS.items():
        if name not in tables:
            lines.append(f"[MISSING TABLE] '{name}' was not loaded (file missing or unreadable).")
            continue
        df = tables[name]
        n_rows = len(df)
        status = "EMPTY" if n_rows == 0 else "ok"
        lines.append(f"[{name}] rows={n_rows} ({status})")

        missing_cols = [c for c in expected_cols if c not in df.columns]
        extra_cols = [c for c in df.columns if c not in expected_cols]
        if missing_cols:
            lines.append(f"  - MISSING COLUMNS: {missing_cols}")
        if extra_cols:
            lines.append(f"  - unexpected/extra columns: {extra_cols}")
        if n_rows == 0:
            lines.append("  - CORE TABLE IS EMPTY - likely an ETL failure, investigate before proceeding.")
    report.add("1. All Tables Present", lines)


# ---------------------------------------------------------------------------
# 2. Missingness
# ---------------------------------------------------------------------------

def check_missingness(tables, report):
    lines = []
    for name, df in tables.items():
        if df.empty:
            continue
        n = len(df)
        # treat empty-string as null too, since we loaded everything as str
        is_blank = lambda col: col.map(lambda x: isinstance(x, str) and x.strip() == "")
        null_mask = df.isna() | df.apply(is_blank)
        null_rates = null_mask.mean().sort_values(ascending=False)

        lines.append(f"[{name}] null rates by column:")
        for col, rate in null_rates.items():
            flag = ""
            required = col in REQUIRED_NOT_NULL.get(name, [])
            if required and rate > 0:
                flag = "  <-- REQUIRED FIELD HAS NULLS"
            elif rate > MISSINGNESS_THRESHOLD:
                flag = f"  <-- exceeds {MISSINGNESS_THRESHOLD:.0%} threshold"
            if rate > 0 or required:
                lines.append(f"    {col:<32} {rate:6.1%} ({int(rate * n)}/{n}){flag}")
        lines.append("")
    report.add("2. Missingness", lines)


# ---------------------------------------------------------------------------
# 3. Date format / logic checks
# ---------------------------------------------------------------------------

def _parse_dates(df, cols):
    """
    Return:
        parsed_df:
            Copy of df with requested date columns converted to datetime.

        parse_failures:
            Dict mapping existing date column -> number of non-null/non-blank
            values that could not be parsed.

        missing_cols:
            List of expected date columns that are not present in df.
    """
    df = df.copy()
    parse_failures = {}
    missing_cols = []

    for col in cols:
        if col not in df.columns:
            missing_cols.append(col)
            continue

        raw = df[col]

        # True nulls and blank strings are treated as missing.
        was_present = raw.notna() & (raw.astype(str).str.strip() != "")

        parsed = pd.to_datetime(raw, errors="coerce")

        # Values that were present but could not be parsed.
        failed = was_present & parsed.isna()

        parse_failures[col] = int(failed.sum())
        df[col] = parsed

    return df, parse_failures, missing_cols


def check_dates(tables, report):
    lines = []
    parsed = {}

    # -----------------------------------------------------------------------
    # Parse configured date columns
    # -----------------------------------------------------------------------
    for name, cols in DATE_COLUMNS.items():
        if name not in tables:
            lines.append(
                f"[{name}] table not found; configured date checks were skipped."
            )
            continue

        df, failures, missing_cols = _parse_dates(tables[name], cols)
        parsed[name] = df

        for col in missing_cols:
            lines.append(f"[{name}.{col}] expected date column not found.")

        for col, n_fail in failures.items():
            if n_fail > 0:
                lines.append(
                    f"[{name}.{col}] {n_fail} value(s) could not be parsed as a date."
                )

    # -----------------------------------------------------------------------
    # observation_period: start <= end, neither in the future
    # -----------------------------------------------------------------------
    if "observation_period" in parsed:
        op = parsed["observation_period"]
        start_col, end_col = "observation_period_start_date", "observation_period_end_date"

        if start_col in op.columns and end_col in op.columns:
            bad_range = op[
                op[start_col].notna() &
                op[end_col].notna() &
                (op[start_col] > op[end_col])
            ]
            if len(bad_range):
                lines.append(
                    f"[observation_period] {len(bad_range)} row(s) have "
                    f"{start_col} after {end_col}."
                )

        for col in (start_col, end_col):
            if col in op.columns:
                future = op[op[col].notna() & (op[col] > TODAY)]
                if len(future):
                    lines.append(
                        f"[observation_period] {len(future)} row(s) have {col} in the future."
                    )

    # -----------------------------------------------------------------------
    # condition_occurrence / measurement: dates not in the future
    # -----------------------------------------------------------------------
    for name, date_col in (
        ("condition_occurrence", "condition_start_date"),
        ("measurement", "measurement_date"),
    ):
        if name not in parsed or date_col not in parsed[name].columns:
            continue
        d = parsed[name]
        future = d[d[date_col].notna() & (d[date_col] > TODAY)]
        if len(future):
            lines.append(
                f"[{name}] {len(future)} row(s) have {date_col} in the future."
            )

    # -----------------------------------------------------------------------
    # person: year_of_birth plausibility
    # -----------------------------------------------------------------------
    person_ids = set()
    if "person" in tables:
        p = tables["person"]
        if "person_id" in p.columns:
            person_ids = set(p["person_id"].dropna())

        if "year_of_birth" in p.columns:
            yob = pd.to_numeric(p["year_of_birth"], errors="coerce")
            bad_yob = p[
                yob.notna() &
                ((yob < 1900) | (yob > TODAY.year))
            ]
            if len(bad_yob):
                lines.append(
                    f"[person] {len(bad_yob)} row(s) have an implausible "
                    f"year_of_birth (<1900 or after {TODAY.year})."
                )

    # -----------------------------------------------------------------------
    # Referential integrity: person_id in child tables must exist in person
    # -----------------------------------------------------------------------
    if "person" in tables:
        for child_name in ("observation_period", "condition_occurrence", "measurement"):
            if child_name not in tables or "person_id" not in tables[child_name].columns:
                continue
            c = tables[child_name]
            orphan_mask = c["person_id"].notna() & ~c["person_id"].isin(person_ids)
            n_orphan = int(orphan_mask.sum())
            if n_orphan:
                lines.append(
                    f"[{child_name}] {n_orphan} row(s) reference a person_id "
                    f"not found in person.csv (orphan foreign key)."
                )
    else:
        lines.append(
            "[person] table not found; referential-integrity checks against "
            "person_id were skipped."
        )

    # -----------------------------------------------------------------------
    # Cross-table: condition/measurement dates should fall within at least
    # one of the person's observation_period date ranges.
    # -----------------------------------------------------------------------
    if (
        "observation_period" in parsed
        and "person_id" in parsed["observation_period"].columns
        and "observation_period_start_date" in parsed["observation_period"].columns
        and "observation_period_end_date" in parsed["observation_period"].columns
    ):
        op = parsed["observation_period"][
            ["person_id", "observation_period_start_date", "observation_period_end_date"]
        ].dropna(subset=["person_id"])

        for child_name, date_col in (
            ("condition_occurrence", "condition_start_date"),
            ("measurement", "measurement_date"),
        ):
            if child_name not in parsed:
                continue
            c = parsed[child_name]
            if "person_id" not in c.columns or date_col not in c.columns:
                continue

            c2 = c[["person_id", date_col]].copy()
            c2["_row_id"] = range(len(c2))

            merged = c2.merge(op, on="person_id", how="left")
            merged["_has_period"] = merged["observation_period_start_date"].notna()
            merged["_within"] = (
                merged["_has_period"] &
                merged[date_col].notna() &
                (merged[date_col] >= merged["observation_period_start_date"]) &
                (merged[date_col] <= merged["observation_period_end_date"])
            )

            within_by_row = merged.groupby("_row_id")["_within"].any()
            has_period_by_row = merged.groupby("_row_id")["_has_period"].any()

            has_date = c2[date_col].notna()
            row_within = c2["_row_id"].map(within_by_row).fillna(False)
            row_has_period = c2["_row_id"].map(has_period_by_row).fillna(False)

            n_outside = int((has_date & row_has_period & ~row_within).sum())
            if n_outside:
                lines.append(
                    f"[{child_name}] {n_outside} row(s) have {date_col} outside "
                    f"all of the person's observation_period date range(s)."
                )

    # -----------------------------------------------------------------------
    # Final report
    # -----------------------------------------------------------------------
    if not lines:
        lines.append("All configured date format and logic checks passed.")

    report.add("3. Date Format & Logic Checks", lines)


# ---------------------------------------------------------------------------
# 4. Mapping rate (fraction successfully mapped to standard OMOP concepts)
# ---------------------------------------------------------------------------

def check_mapping_rate(tables, report):
    lines = []

    for name, concept_col in CONCEPT_COLUMNS.items():
        if name not in tables:
            continue
        df = tables[name]
        if concept_col not in df.columns or df.empty:
            continue
        n = len(df)
        vals = pd.to_numeric(df[concept_col], errors="coerce")
        unmapped = vals.isna() | (vals == 0)
        n_unmapped = int(unmapped.sum())
        pct = n_unmapped / n if n else 0
        flag = "  <-- HIGH UNMAPPED RATE" if pct > MAPPING_RATE_THRESHOLD else ""
        lines.append(
            f"[{name}] {concept_col}: {n_unmapped}/{n} unmapped ({pct:.1%}){flag}"
        )

        # Breakdown by *_source_value, to help pinpoint where mapping is weak.
        source_col = TABLE_SOURCE_VALUE_COL.get(name)
        if source_col and source_col in df.columns:
            breakdown = (
                df.assign(_unmapped=unmapped)
                .groupby(source_col)["_unmapped"]
                .agg(["sum", "count"])
                .rename(columns={"sum": "unmapped", "count": "total"})
            )
            breakdown["pct"] = breakdown["unmapped"] / breakdown["total"]
            breakdown = breakdown.sort_values("pct", ascending=False)
            top_offenders = [
                (val, row) for val, row in breakdown.iterrows() if row["unmapped"] > 0
            ][:15]
            for val, row in top_offenders:
                lines.append(
                    f"    by {source_col}={val!r}: "
                    f"{int(row['unmapped'])}/{int(row['total'])} unmapped ({row['pct']:.1%})"
                )
            if len(breakdown[breakdown["unmapped"] > 0]) > len(top_offenders):
                lines.append("    ... (truncated to top 15 offending source values)")

    # Demographic concept columns on person (no source_value breakdown available).
    if "person" in tables:
        df = tables["person"]
        n = len(df)
        for concept_col in PERSON_CONCEPT_COLUMNS:
            if concept_col not in df.columns or df.empty:
                continue
            vals = pd.to_numeric(df[concept_col], errors="coerce")
            unmapped = vals.isna() | (vals == 0)
            n_unmapped = int(unmapped.sum())
            pct = n_unmapped / n if n else 0
            flag = "  <-- HIGH UNMAPPED RATE" if pct > MAPPING_RATE_THRESHOLD else ""
            lines.append(
                f"[person] {concept_col}: {n_unmapped}/{n} unmapped ({pct:.1%}){flag}"
            )

    report.add("4. Mapping Rate (concept_id = 0 / null)", lines)


# ---------------------------------------------------------------------------
# Per-site orchestration
# ---------------------------------------------------------------------------

def run_qc_for_site(site_name, paths):
    """Load one site's four tables and run all checks. Returns (QCReport, tables)."""
    tables = load_tables(paths)

    report = QCReport()
    check_tables_present(tables, report)
    check_missingness(tables, report)
    check_dates(tables, report)
    check_mapping_rate(tables, report)
    return report, tables


def build_cross_site_summary(site_results):
    """site_results: list of (site_name, tables dict). Returns list of summary lines."""
    lines = []
    header = f"{'site':<24}" + "".join(f"{name:<22}" for name in EXPECTED_SCHEMAS)
    lines.append(header)
    lines.append("-" * len(header))
    for site_name, tables in site_results:
        row = f"{site_name:<24}"
        for name in EXPECTED_SCHEMAS:
            if name not in tables:
                cell = "MISSING"
            else:
                cell = f"{len(tables[name])} rows"
            row += f"{cell:<22}"
        lines.append(row)
    return lines


# ---------------------------------------------------------------------------
# Site discovery
# ---------------------------------------------------------------------------

def discover_sites(args):
    """Returns an ordered dict: site_name -> dict(table_name -> path or None)."""
    sites = {}

    # Explicit single-table overrides (single site only).
    override_paths = {
        "person": args.person,
        "observation_period": args.observation_period,
        "condition_occurrence": args.condition_occurrence,
        "measurement": args.measurement,
    }
    if any(override_paths.values()):
        site_name = "cohort"
        sites[site_name] = override_paths

    # Explicit --site NAME=PATH entries.
    for spec in (args.site or []):
        if "=" not in spec:
            print(
                f"WARNING: --site value '{spec}' is not in NAME=PATH form, skipping.",
                file=sys.stderr,
            )
            continue
        name, path = spec.split("=", 1)
        name, path = name.strip(), path.strip()
        if not os.path.isdir(path):
            print(f"WARNING: --site '{name}' path '{path}' is not a directory, skipping.", file=sys.stderr)
            continue
        sites[name] = resolve_table_paths(path)

    # --dir: either a single site's folder, or a parent folder of sites.
    if args.dir:
        d = args.dir
        if not os.path.isdir(d):
            print(f"WARNING: --dir '{d}' is not a directory, skipping.", file=sys.stderr)
        else:
            direct_paths = resolve_table_paths(d)
            if any(direct_paths.values()):
                # --dir points straight at one site's CSVs.
                name = os.path.basename(os.path.normpath(d)) or "cohort"
                sites[name] = direct_paths
            else:
                # Treat each subdirectory as its own site.
                for entry in sorted(os.listdir(d)):
                    sub = os.path.join(d, entry)
                    if not os.path.isdir(sub):
                        continue
                    sub_paths = resolve_table_paths(sub)
                    if any(sub_paths.values()):
                        sites[entry] = sub_paths
                if not any(
                    any(p.values()) for p in sites.values()
                ) and not sites:
                    print(
                        f"WARNING: no site subdirectories with recognizable "
                        f"OMOP CSVs found under '{d}'.",
                        file=sys.stderr,
                    )

    return sites


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=(
            "OMOP CDM QC for person/observation_period/condition_occurrence/"
            "measurement CSVs, single-site or multi-site."
        )
    )
    ap.add_argument(
        "--dir",
        help=(
            "Directory containing the four CSVs for one site, OR a parent "
            "directory whose subdirectories are each a separate site."
        ),
    )
    ap.add_argument(
        "--site",
        action="append",
        metavar="NAME=PATH",
        help="Explicit site: a name and the directory holding its 4 CSVs. Repeatable.",
    )
    ap.add_argument("--person", help="Path to person.csv (single-site override)")
    ap.add_argument("--observation_period", help="Path to observation_period.csv (single-site override)")
    ap.add_argument("--condition_occurrence", help="Path to condition_occurrence.csv (single-site override)")
    ap.add_argument("--measurement", help="Path to measurement.csv (single-site override)")
    ap.add_argument("--out", default="qc_report.txt", help="Path to write the text report")
    args = ap.parse_args()

    sites = discover_sites(args)

    if not sites:
        ap.error(
            "Provide --dir (single site, or a parent of multiple site folders), "
            "--site NAME=PATH (repeatable), or the single-table overrides "
            "(--person/--observation_period/--condition_occurrence/--measurement)."
        )

    site_results = []  # (site_name, tables) for cross-site summary
    report_blocks = []  # rendered per-site report text

    for site_name, paths in sites.items():
        report, tables = run_qc_for_site(site_name, paths)
        site_results.append((site_name, tables))

        site_header = [
            "=" * 78,
            f"SITE: {site_name}",
            "=" * 78,
        ]
        report_blocks.append(report.render(header_lines=site_header))

    top_header = [
        "=" * 78,
        "OMOP CDM QC REPORT",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Sites: {len(site_results)} ({', '.join(name for name, _ in site_results)})",
        "=" * 78,
    ]

    summary_lines = ["", "## 0. Cross-Site Summary (row counts per table)", "-" * 78]
    summary_lines += [f"  {line}" for line in build_cross_site_summary(site_results)]

    text = "\n".join(top_header) + "\n" + "\n".join(summary_lines) + "\n\n" + "\n\n".join(report_blocks)

    with open(args.out, "w") as f:
        f.write(text + "\n")

    print(text)
    print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
