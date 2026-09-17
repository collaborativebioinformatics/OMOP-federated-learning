#!/usr/bin/env python3
"""
omop_qc.py
==========

Lightweight OMOP-style QC pass for three flat CSV extracts:

    patients.csv
    diagnoses.csv
    biomarkers.csv

This mirrors the four checks in the QC checklist (missingness, table
presence, date validity/logic, and mapping rate to standard OMOP
concepts) but is adapted to work directly on CSVs with pandas instead
of a live OMOP CDM database (example used: 3 csv files within synthea_cohorts/cohort_1/data).

Usage:
    python omop_qc.py --dir /path/to/csvs --out qc_report.txt
    python omop_qc.py --patients patients.csv --diagnoses diagnoses.csv --biomarkers biomarkers.csv
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

TODAY = pd.Timestamp(datetime.now().date())

# ---------------------------------------------------------------------------
# Configuration: what "required" / "key" / "concept" / "date" columns exist
# for each table. Edit these if your schema changes.
# ---------------------------------------------------------------------------

EXPECTED_SCHEMAS = {
    "patients": [
        "patient_id", "birth_date", "sex", "race", "ethnicity", "index_date",
        "age_at_index", "death_event", "death_date", "followup_end_date",
        "survival_time_days",
    ],
    "diagnoses": [
        "diagnosis_id", "patient_id", "encounter_id", "diagnosis_start_date",
        "diagnosis_end_date", "diagnosis_name", "source_vocabulary",
        "source_code", "omop_condition_concept_id",
    ],
    "biomarkers": [
        "measurement_id", "patient_id", "encounter_id", "measurement_date",
        "days_from_index", "biomarker_name", "value_as_number",
        "unit_source_value", "source_vocabulary", "source_code",
        "source_description", "omop_measurement_concept_id",
    ],
}

# Fields that should essentially never be null (ETL failure if they are).
REQUIRED_NOT_NULL = {
    "patients": ["patient_id", "birth_date", "index_date"],
    "diagnoses": ["diagnosis_id", "patient_id", "diagnosis_start_date"],
    "biomarkers": ["measurement_id", "patient_id", "measurement_date"],
}

# Date columns per table, used for both parse-validity and logic checks.
DATE_COLUMNS = {
    "patients": ["birth_date", "index_date", "death_date", "followup_end_date"],
    "diagnoses": ["diagnosis_start_date", "diagnosis_end_date"],
    "biomarkers": ["measurement_date"],
}

# concept_id columns used to compute the OMOP mapping rate.
CONCEPT_COLUMNS = {
    "diagnoses": "omop_condition_concept_id",
    "biomarkers": "omop_measurement_concept_id",
}

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

    def render(self):
        out = []
        out.append("=" * 78)
        out.append("OMOP-STYLE QC REPORT")
        out.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
        out.append("=" * 78)
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
# 1. Load tables + confirm all expected tables/columns are present
# ---------------------------------------------------------------------------

def load_tables(paths):
    """paths: dict of table_name -> csv path. Returns dict of DataFrames."""
    tables = {}
    for name, path in paths.items():
        if path is None:
            continue
        if not os.path.isfile(path):
            print(f"WARNING: {name} file not found at {path}, skipping.", file=sys.stderr)
            continue
        df = pd.read_csv(path, dtype=str)  # read as str first; we parse dates/numbers explicitly
        df.columns = [c.strip() for c in df.columns]
        tables[name] = df
    return tables


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

        # Expected table is missing.
        if name not in tables:
            lines.append(
                f"[{name}] table not found; configured date checks were skipped."
            )
            continue

        df, failures, missing_cols = _parse_dates(tables[name], cols)
        parsed[name] = df

        # Expected date columns missing from the table.
        for col in missing_cols:
            lines.append(
                f"[{name}.{col}] expected date column not found."
            )

        # Date parsing failures.
        for col, n_fail in failures.items():
            if n_fail > 0:
                lines.append(
                    f"[{name}.{col}] {n_fail} value(s) could not be parsed as a date."
                )

    # -----------------------------------------------------------------------
    # Patient-level logical / plausibility checks
    # -----------------------------------------------------------------------
    if "patients" in parsed:
        p = parsed["patients"]

        # Birth date cannot be in the future.
        if "birth_date" in p.columns:
            future_birth = p[
                p["birth_date"].notna() &
                (p["birth_date"] > TODAY)
            ]

            if len(future_birth):
                lines.append(
                    f"[patients] {len(future_birth)} row(s) have "
                    f"birth_date in the future."
                )

        # Death cannot occur before index date.
        if "death_date" in p.columns and "index_date" in p.columns:
            bad_death = p[
                p["death_date"].notna() &
                p["index_date"].notna() &
                (p["death_date"] < p["index_date"])
            ]

            if len(bad_death):
                lines.append(
                    f"[patients] {len(bad_death)} row(s) have "
                    f"death_date before index_date."
                )

        # death_event=true should have a death_date.
        if (
            "death_event" in tables["patients"].columns
            and "death_date" in p.columns
        ):
            death_flag = (
                tables["patients"]["death_event"]
                .astype(str)
                .str.strip()
                .str.lower()
            )

            flagged_dead = death_flag.isin(
                ["1", "true", "yes", "y"]
            )

            missing_death_date = (
                flagged_dead &
                p["death_date"].isna()
            )

            if missing_death_date.sum():
                lines.append(
                    f"[patients] {int(missing_death_date.sum())} row(s) have "
                    f"death_event=true but no death_date."
                )

        # Follow-up cannot end before index date.
        if "followup_end_date" in p.columns and "index_date" in p.columns:
            bad_followup = p[
                p["followup_end_date"].notna() &
                p["index_date"].notna() &
                (p["followup_end_date"] < p["index_date"])
            ]

            if len(bad_followup):
                lines.append(
                    f"[patients] {len(bad_followup)} row(s) have "
                    f"followup_end_date before index_date."
                )

    # -----------------------------------------------------------------------
    # Diagnosis-level logical checks
    # -----------------------------------------------------------------------
    if "diagnoses" in parsed:
        d = parsed["diagnoses"]

        # Diagnosis start must not be after diagnosis end.
        if (
            "diagnosis_start_date" in d.columns
            and "diagnosis_end_date" in d.columns
        ):
            bad_range = d[
                d["diagnosis_start_date"].notna() &
                d["diagnosis_end_date"].notna() &
                (d["diagnosis_start_date"] > d["diagnosis_end_date"])
            ]

            if len(bad_range):
                lines.append(
                    f"[diagnoses] {len(bad_range)} row(s) have "
                    f"diagnosis_start_date after diagnosis_end_date."
                )

        # Diagnosis start cannot be in the future.
        if "diagnosis_start_date" in d.columns:
            future_start = d[
                d["diagnosis_start_date"].notna() &
                (d["diagnosis_start_date"] > TODAY)
            ]

            if len(future_start):
                lines.append(
                    f"[diagnoses] {len(future_start)} row(s) have "
                    f"diagnosis_start_date in the future."
                )

    # -----------------------------------------------------------------------
    # Cross-table checks
    #
    # Diagnosis / biomarker dates should fall within:
    #     birth_date --> death_date OR followup_end_date
    # -----------------------------------------------------------------------
    if "patients" in parsed:
        p = parsed["patients"]

        required_patient_cols = {
            "patient_id",
            "birth_date",
            "followup_end_date",
            "death_date",
        }

        missing_patient_cols = required_patient_cols - set(p.columns)

        if missing_patient_cols:
            lines.append(
                "[patients] cross-table date checks skipped because these "
                "columns are missing: "
                + ", ".join(sorted(missing_patient_cols))
                + "."
            )

        else:
            p_window = p[
                [
                    "patient_id",
                    "birth_date",
                    "followup_end_date",
                    "death_date",
                ]
            ].copy()

            # Death date takes precedence over follow-up end date.
            p_window["_window_end"] = p_window["death_date"].where(
                p_window["death_date"].notna(),
                p_window["followup_end_date"],
            )

            for child_name, date_col in (
                ("diagnoses", "diagnosis_start_date"),
                ("biomarkers", "measurement_date"),
            ):
                if child_name not in parsed:
                    continue

                c = parsed[child_name]

                # Required patient_id column missing.
                if "patient_id" not in c.columns:
                    lines.append(
                        f"[{child_name}] cross-table date check skipped because "
                        f"patient_id is missing."
                    )
                    continue

                # Required date column missing.
                if date_col not in c.columns:
                    lines.append(
                        f"[{child_name}] cross-table date check skipped because "
                        f"{date_col} is missing."
                    )
                    continue

                merged = c.merge(
                    p_window,
                    on="patient_id",
                    how="left",
                    suffixes=("", "_pt"),
                )

                # Date before patient's birth.
                before_birth = merged[
                    merged["birth_date"].notna() &
                    merged[date_col].notna() &
                    (merged[date_col] < merged["birth_date"])
                ]

                if len(before_birth):
                    lines.append(
                        f"[{child_name}] {len(before_birth)} row(s) have "
                        f"{date_col} before the patient's birth_date."
                    )

                # Date after death/follow-up.
                after_window = merged[
                    merged["_window_end"].notna() &
                    merged[date_col].notna() &
                    (merged[date_col] > merged["_window_end"])
                ]

                if len(after_window):
                    lines.append(
                        f"[{child_name}] {len(after_window)} row(s) have "
                        f"{date_col} after the patient's "
                        f"death_date/followup_end_date."
                    )

                # Patient referenced in child table but absent from patients.
                unmatched = merged[
                    merged["patient_id"].notna() &
                    merged["birth_date"].isna()
                ]

                if len(unmatched):
                    lines.append(
                        f"[{child_name}] {len(unmatched)} row(s) reference a "
                        f"patient_id not found in patients.csv "
                        f"(orphan foreign key)."
                    )

    # -----------------------------------------------------------------------
    # Final report
    # -----------------------------------------------------------------------
    if not lines:
        lines.append(
            "All configured date format and logic checks passed."
        )

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

        # Breakdown by source vocabulary, to help pinpoint where mapping is weak.
        if "source_vocabulary" in df.columns:
            breakdown = (
                df.assign(_unmapped=unmapped)
                .groupby("source_vocabulary")["_unmapped"]
                .agg(["sum", "count"])
                .rename(columns={"sum": "unmapped", "count": "total"})
            )
            breakdown["pct"] = breakdown["unmapped"] / breakdown["total"]
            breakdown = breakdown.sort_values("pct", ascending=False)
            for vocab, row in breakdown.iterrows():
                if row["unmapped"] > 0:
                    lines.append(
                        f"    by source_vocabulary={vocab!r}: "
                        f"{int(row['unmapped'])}/{int(row['total'])} unmapped ({row['pct']:.1%})"
                    )
    report.add("4. Mapping Rate (concept_id = 0 / null)", lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="OMOP-style QC for patients/diagnoses/biomarkers CSVs.")
    ap.add_argument("--dir", help="Directory containing patients.csv, diagnoses.csv, biomarkers.csv")
    ap.add_argument("--patients", help="Path to patients.csv (overrides --dir)")
    ap.add_argument("--diagnoses", help="Path to diagnoses.csv (overrides --dir)")
    ap.add_argument("--biomarkers", help="Path to biomarkers.csv (overrides --dir)")
    ap.add_argument("--out", default="qc_report.txt", help="Path to write the text report")
    args = ap.parse_args()

    def resolve(name, override):
        if override:
            return override
        if args.dir:
            return os.path.join(args.dir, f"{name}.csv")
        return None

    paths = {
        "patients": resolve("patients", args.patients),
        "diagnoses": resolve("diagnoses", args.diagnoses),
        "biomarkers": resolve("biomarkers", args.biomarkers),
    }

    if not any(paths.values()):
        ap.error("Provide --dir, or at least one of --patients/--diagnoses/--biomarkers")

    tables = load_tables(paths)

    report = QCReport()
    check_tables_present(tables, report)
    check_missingness(tables, report)
    check_dates(tables, report)
    check_mapping_rate(tables, report)

    text = report.render()
    with open(args.out, "w") as f:
        f.write(text + "\n")

    print(text)
    print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
