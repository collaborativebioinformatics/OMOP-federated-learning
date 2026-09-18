#!/usr/bin/env python3
"""Disease-specific mapping QC and descriptive yearly lab trends for the UKB pilot."""

import argparse
import collections
import csv
import json
import math
import os
import statistics
import tempfile
from pathlib import Path

from summarize import ASSAYS, DISEASES, summarize


cache_dir = Path(tempfile.gettempdir()) / "solvi_plot_cache"
cache_dir.mkdir(exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
os.environ.setdefault("MPLCONFIGDIR", str(cache_dir / "matplotlib"))
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as error:
    raise SystemExit("matplotlib is required; use a Python with matplotlib installed") from error


MIN_YEAR_PARTICIPANTS = 5
NAVY, TEAL, GREY, BG = "#24364b", "#278f86", "#64748b", "#f5f8fb"


def rows(path, delimiter=","):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream, delimiter=delimiter)


def disease_for_code(code):
    normalized = code.strip().upper().replace(".", "")
    return [name for name, prefixes in DISEASES.items()
            if normalized.startswith(prefixes)]


def source_lab_counts(source, disease_eids):
    counts = collections.Counter()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = set(ASSAYS.values())
        lab_columns = [(column, column.split("-", 1)[0]) for column in reader.fieldnames or []
                       if column.split("-", 1)[0] in fields and "-" in column]
        for row in reader:
            eid = row["EID"].strip()
            groups = [name for name, members in disease_eids.items() if eid in members]
            if not groups:
                continue
            for column, field_id in lab_columns:
                if row[column].strip():
                    for name in groups:
                        counts[(name, field_id)] += 1
    return counts


def quantile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def build_qc(source, run):
    omop = run / "omop"
    spec = json.loads((Path(__file__).resolve().parents[1] / "specs" /
                       "ukb_ad_pd_t2d_labs_v1.json").read_text())
    expected = {item["field_id"]: (item["target_concept_ids"][0], item["unit_concept_id"])
                for item in spec["fields"] if item["kind"] == "numeric"}
    people = {row["person_id"]: row["person_source_value"]
              for row in rows(omop / "person.csv")}
    periods = {row["person_id"]: row for row in rows(omop / "observation_period.csv")}
    groups = {name: set() for name in DISEASES}
    disease_eids = {name: set() for name in DISEASES}
    condition_rows = collections.Counter()
    mapped_condition_rows = collections.Counter()
    for row in rows(omop / "condition_occurrence.csv"):
        for name in disease_for_code(row["condition_source_value"]):
            groups[name].add(row["person_id"])
            disease_eids[name].add(people[row["person_id"]])
            condition_rows[name] += 1
            if int(row["condition_concept_id"]):
                mapped_condition_rows[name] += 1
    source_counts = source_lab_counts(source, disease_eids)
    written_counts = collections.Counter()
    mapped_counts = collections.Counter()
    invalid_counts = collections.Counter()
    zero_type_counts = collections.Counter()
    zero_source_concept_counts = collections.Counter()
    numeric_value_concept_zero_counts = collections.Counter()
    year_values = collections.defaultdict(list)
    year_people = collections.defaultdict(set)
    for row in rows(omop / "measurement.csv"):
        field_id = row["measurement_source_value"]
        if field_id not in expected:
            continue
        for name, members in groups.items():
            if row["person_id"] not in members:
                continue
            key = (name, field_id)
            written_counts[key] += 1
            zero_type_counts[key] += row["measurement_type_concept_id"] == "0"
            zero_source_concept_counts[key] += row["measurement_source_concept_id"] == "0"
            numeric_value_concept_zero_counts[key] += row["value_as_concept_id"] == "0"
            target, unit = expected[field_id]
            try:
                value = float(row["value_as_number"])
                year = int(row["measurement_date"][:4])
                valid = (math.isfinite(value) and year > 1900
                         and int(row["measurement_concept_id"]) == target
                         and int(row["unit_concept_id"]) == unit)
            except (ValueError, TypeError):
                valid = False
            if not valid:
                invalid_counts[key] += 1
                continue
            mapped_counts[key] += 1
            year_values[(name, field_id, year)].append(value)
            year_people[(name, field_id, year)].add(row["person_id"])

    overview = {item["variable"]: item for item in summarize(source, run)["variables"]
                if item["variable"] in DISEASES}
    mapping_report = json.loads((omop / "mapping_report.json").read_text())
    mapped_by_code = {item["source_code"]: item["records"]
                      for item in mapping_report["mapped_source_events_by_key"]
                      if item["field_id"] == "41270"}
    code_qc = []
    for row in rows(run / "mapping_review.csv"):
        if row["field_id"] != "41270":
            continue
        for name in disease_for_code(row["source_code"]):
            mapped = mapped_by_code.get(row["source_code"], 0)
            code_qc.append({"disease": name, "source_code": row["source_code"],
                            "source_records": int(row["records"]),
                            "dated_records": int(row["dated_records"]),
                            "mapped_source_records": mapped,
                            "unmapped_dated_records": int(row["dated_records"]) - mapped,
                            "decision": row["decision"],
                            "approved_target_ids": row["approved_target_ids"]})
    diagnosis_qc = []
    lab_qc = []
    yearly = []
    for name in DISEASES:
        item = overview[name]
        group_periods = [periods[person] for person in groups[name] if person in periods]
        diagnosis_qc.append({"disease": name, "rule": item["source_rule"],
                             "participants": len(groups[name]),
                             "source_records": item["source_records"],
                             "dated_records": item["dated_records"],
                             "omop_rows": condition_rows[name],
                             "mapped_rows": mapped_condition_rows[name],
                             "unmapped_source_records": item["source_records"] - mapped_condition_rows[name],
                             "observation_period_rows": len(group_periods),
                             "missing_observation_periods": len(groups[name]) - len(group_periods),
                             "single_day_observation_periods": sum(
                                 row["observation_period_start_date"] == row["observation_period_end_date"]
                                 for row in group_periods),
                             "period_type_concept_ids": ";".join(sorted({
                                 row["period_type_concept_id"] for row in group_periods}))})
        for label, field_id in ASSAYS.items():
            key = (name, field_id)
            values = [value for (group, field, _), bucket in year_values.items()
                      if (group, field) == key for value in bucket]
            source_n = source_counts[key]
            mapped_n = mapped_counts[key]
            lab_qc.append({"disease": name, "variable": label, "ukb_field": field_id,
                           "omop_concept_id": expected[field_id][0],
                           "unit_concept_id": expected[field_id][1],
                           "participants": len(groups[name]),
                           "source_values": source_n, "omop_rows": written_counts[key],
                           "mapped_rows_with_expected_unit": mapped_n,
                           "unmapped_or_invalid_rows": source_n - mapped_n,
                           "zero_measurement_type_rows": zero_type_counts[key],
                           "zero_source_concept_rows": zero_source_concept_counts[key],
                           "numeric_value_concept_zero_rows": numeric_value_concept_zero_counts[key],
                           "median_mmol_l": statistics.median(values) if values else None,
                           "p25_mmol_l": quantile(values, .25),
                           "p75_mmol_l": quantile(values, .75)})
            for group, field, year in sorted(year_values):
                if (group, field) != key:
                    continue
                bucket = year_values[(group, field, year)]
                people_n = len(year_people[(group, field, year)])
                yearly.append({"disease": name, "variable": label, "ukb_field": field_id,
                               "assessment_year": year, "measurements": len(bucket),
                               "participants": people_n,
                               "median_mmol_l": statistics.median(bucket),
                               "p25_mmol_l": quantile(bucket, .25),
                               "p75_mmol_l": quantile(bucket, .75),
                               "plotted": people_n >= MIN_YEAR_PARTICIPANTS})
    return diagnosis_qc, code_qc, lab_qc, yearly, invalid_counts


def write_csv(path, data):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def plot_disease(name, diagnosis, labs, yearly, output):
    fig, axes = plt.subplots(3, 2, figsize=(13, 12), layout="constrained")
    fig.set_facecolor(BG)
    fig.suptitle(f"{name} | OMOP QC and laboratory trends", color=NAVY,
                 fontsize=20, fontweight="bold")
    for ax, lab in zip(axes.flat, labs):
        ax.set_facecolor("white")
        all_points = [item for item in yearly if item["disease"] == name
                      and item["ukb_field"] == lab["ukb_field"]]
        points = [item for item in all_points if item["plotted"]]
        sparse = [item for item in all_points if not item["plotted"]]
        ax.set_title(lab["variable"], loc="left", fontsize=12, fontweight="bold", color=NAVY)
        if points:
            for left, right in zip(points, points[1:]):
                if right["assessment_year"] - left["assessment_year"] == 1:
                    ax.plot([left["assessment_year"], right["assessment_year"]],
                            [left["median_mmol_l"], right["median_mmol_l"]],
                            color=TEAL, linewidth=2)
            for item in points:
                ax.vlines(item["assessment_year"], item["p25_mmol_l"],
                          item["p75_mmol_l"], color=TEAL, alpha=.65, linewidth=5)
                ax.scatter(item["assessment_year"], item["median_mmol_l"],
                           color=TEAL, s=34, zorder=3)
                ax.annotate(f"n={item['participants']}",
                            (item["assessment_year"], item["median_mmol_l"]),
                            xytext=(0, 9), textcoords="offset points", ha="center",
                            fontsize=8, color=GREY)
        else:
            ax.text(.5, .5, f"No year with ≥{MIN_YEAR_PARTICIPANTS} participants",
                    transform=ax.transAxes, ha="center", va="center", color=GREY)
        for item in sparse:
            ax.scatter(item["assessment_year"], item["median_mmol_l"],
                       facecolor="white", edgecolor=GREY, s=34, zorder=3)
            ax.annotate(f"n={item['participants']}",
                        (item["assessment_year"], item["median_mmol_l"]),
                        xytext=(0, 9), textcoords="offset points", ha="center",
                        fontsize=8, color=GREY)
        ax.set_xticks([item["assessment_year"] for item in all_points])
        ax.set_ylabel("Median mmol/L (whisker: IQR)", fontsize=9)
        ax.set_xlabel("Assessment year", fontsize=9)
        ax.grid(alpha=.18)
        ax.spines[["top", "right"]].set_visible(False)
    ax = axes.flat[-1]
    ax.set_axis_off()
    mapped_labs = sum(item["mapped_rows_with_expected_unit"] for item in labs)
    source_labs = sum(item["source_values"] for item in labs)
    text = (f"Disease group: {diagnosis['participants']:,} participants\n"
            f"Diagnosis: {diagnosis['mapped_rows']:,}/{diagnosis['source_records']:,} mapped records\n"
            f"Five labs: {mapped_labs:,}/{source_labs:,} mapped values\n\n"
            f"One-day observation periods: {diagnosis['single_day_observation_periods']:,}"
            f"/{diagnosis['observation_period_rows']:,}\n"
            "Lab provenance type: 0 in this pilot\n\n"
            "Groups are people ever coded with this disease.\n"
            "Yearly points are cross-sectional medians, not\n"
            "within-person trajectories. Hollow points have fewer\n"
            f"than {MIN_YEAR_PARTICIPANTS} participants and are not linked.\n"
            "Dates are assessment-date proxies.")
    ax.text(.04, .92, text, transform=ax.transAxes, va="top", color=NAVY,
            fontsize=10, linespacing=1.65)
    fig.savefig(output / f"{name}_qc.png", dpi=170)
    fig.savefig(output / f"{name}_qc.pdf")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    output = args.run / "disease_qc"
    output.mkdir(exist_ok=True)
    diagnosis, codes, labs, yearly, invalid = build_qc(args.source, args.run)
    write_csv(output / "diagnosis_coverage.csv", diagnosis)
    write_csv(output / "diagnosis_code_coverage.csv", codes)
    write_csv(output / "lab_coverage.csv", labs)
    write_csv(output / "yearly_lab_trends.csv", yearly)
    for row in diagnosis:
        name = row["disease"]
        disease_labs = [item for item in labs if item["disease"] == name]
        write_csv(output / f"{name}_codes.csv", [item for item in codes if item["disease"] == name])
        write_csv(output / f"{name}_lab_coverage.csv", disease_labs)
        write_csv(output / f"{name}_yearly_lab_trends.csv",
                  [item for item in yearly if item["disease"] == name])
        plot_disease(name, row, disease_labs, yearly, output)
    report = {"run": str(args.run), "min_year_participants": MIN_YEAR_PARTICIPANTS,
              "diagnosis": diagnosis, "diagnosis_codes": codes, "lab": labs,
              "invalid_measurement_rows_by_group_field": [
                  {"disease": name, "ukb_field": field, "rows": count}
                  for (name, field), count in sorted(invalid.items())],
              "note": "Descriptive assessment-year trends for ever-coded groups; not longitudinal disease effects."}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "diagnosis": diagnosis,
                      "lab_rows": len(labs), "yearly_rows": len(yearly)}, indent=2))
    if any(item["unmapped_source_records"] for item in diagnosis) or any(
            item["unmapped_or_invalid_rows"] for item in labs):
        raise SystemExit("Disease-specific mapping coverage has gaps; inspect report.json")


if __name__ == "__main__":
    main()
