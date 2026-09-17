#!/usr/bin/env python3
"""Create a graphical QC report for the UKB synthetic-to-OMOP pilot."""

import argparse
import collections
import csv
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ukb_mpl_cache"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


FIELD_LABELS = {
    "31": "Sex",
    "34": "Birth year",
    "53": "Assessment date",
    "21001": "BMI",
    "4080": "Systolic BP",
    "41270": "E11 code",
    "41280": "E11 date",
}
NAVY = "#20324d"
BLUE = "#3977ad"
TEAL = "#2d9c95"
ORANGE = "#d9893c"
GREY = "#b7c1cb"


def read_csv(path, delimiter=","):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=delimiter)
        return reader.fieldnames, list(reader)


def summarize(source_path, omop_dir):
    header, people = read_csv(source_path, "\t")
    if not header or "EID" not in header:
        raise ValueError("UKB source extract has no EID column")
    source_columns = {
        field: [name for name in header if name.startswith(field + "-")]
        for field in FIELD_LABELS
    }
    coverage = {
        field: sum(any(row[name].strip() for name in names) for row in people)
        for field, names in source_columns.items()
    }
    e11_codes = collections.Counter()
    e11_people = 0
    e11_people_with_date = 0
    e11_dated = 0
    for row in people:
        person_codes = []
        person_has_date = False
        for name in source_columns["41270"]:
            code = row[name].strip().upper().replace(".", "")
            if not code.startswith("E11"):
                continue
            person_codes.append(code)
            e11_codes[code] += 1
            date_name = "41280-" + name.split("-", 1)[1]
            if row.get(date_name, "").strip():
                e11_dated += 1
                person_has_date = True
        e11_people += bool(person_codes)
        e11_people_with_date += person_has_date
    coverage["41270"] = e11_people
    coverage["41280"] = e11_people_with_date
    _, measurements = read_csv(omop_dir / "measurement.csv")
    _, conditions = read_csv(omop_dir / "condition_occurrence.csv")
    _, persons = read_csv(omop_dir / "person.csv")
    values = collections.defaultdict(list)
    for row in measurements:
        values[row["measurement_source_value"]].append(float(row["value_as_number"]))
    mapped = sum(row["condition_concept_id"] == "201826" for row in conditions)
    if len(persons) != len(people) or len(conditions) != mapped or mapped != e11_dated:
        raise ValueError("E11-only source and OMOP rows do not reconcile")
    gender = collections.Counter(row["gender_concept_id"] for row in persons)
    return {
        "participants": len(people),
        "coverage": coverage,
        "e11_people": e11_people,
        "values": values,
        "measurement_rows": len(measurements),
        "e11_source_records": sum(e11_codes.values()),
        "e11_dated_records": e11_dated,
        "e11_mapped_records": mapped,
        "e11_code_counts": dict(e11_codes),
        "gender_concept_counts": dict(gender),
    }


def style_axes(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(colors=NAVY, labelsize=9)
    ax.title.set_color(NAVY)
    ax.set_facecolor("white")


def plot_distribution(ax, values, label, unit, bounds, color):
    style_axes(ax)
    ax.set_title(label, loc="left", weight="bold", fontsize=12, pad=12)
    if not values:
        ax.text(.5, .5, "No mapped values", ha="center", va="center", transform=ax.transAxes)
        return 0
    low, high = bounds
    outliers = sum(v < low or v > high for v in values)
    plotted = [v for v in values if low <= v <= high]
    ax.hist(plotted, bins=22, color=color, alpha=.88, edgecolor="white", linewidth=.6)
    ax.set_xlabel(unit, fontsize=9)
    ax.set_ylabel("Measurements", fontsize=9)
    ax.text(.98, .96, f"n = {len(values):,}\nOutside QC range: {outliers:,}",
            ha="right", va="top", transform=ax.transAxes, fontsize=9,
            bbox={"facecolor": "white", "edgecolor": "#dce3e9", "boxstyle": "round,pad=.4"})
    return outliers


def build_figure(summary):
    plt.rcParams.update({"font.family": "DejaVu Sans", "savefig.facecolor": "#f5f8fb"})
    fig, axes = plt.subplots(3, 2, figsize=(14, 13))
    fig.set_facecolor("#f5f8fb")
    fig.subplots_adjust(left=.12, right=.96, top=.87, bottom=.09, hspace=.58, wspace=.38)
    fig.suptitle("UK Biobank synthetic → OMOP | pilot QC", fontsize=20,
                 color=NAVY, weight="bold", x=.12, y=.965, ha="left")
    fig.text(.12, .925,
             f"{summary['participants']:,} E11-positive participants  •  {summary['e11_mapped_records']:,} mapped E11 records  •  "
             f"{summary['measurement_rows']:,} measurements",
             fontsize=11, color="#526273")

    ax = axes[0, 0]
    style_axes(ax)
    labels = list(FIELD_LABELS.values())
    fields = list(FIELD_LABELS)
    fractions = [summary["coverage"][field] / summary["participants"] for field in fields]
    bars = ax.barh(labels[::-1], fractions[::-1], color=[TEAL if x == 1 else BLUE for x in fractions[::-1]])
    ax.set_xlim(0, 1.12)
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.set_title("Source field availability · participants", loc="left", weight="bold", fontsize=12, pad=12)
    for bar, field in zip(bars, fields[::-1]):
        ax.text(bar.get_width() + .015, bar.get_y() + bar.get_height() / 2,
                f"{summary['coverage'][field]}/{summary['participants']}", va="center", fontsize=9)
    ax.grid(axis="x", alpha=.18)
    ax.set_axisbelow(True)

    ax = axes[0, 1]
    style_axes(ax)
    codes = sorted(summary["e11_code_counts"].items(), key=lambda item: item[1], reverse=True)
    labels = [code[:3] + "." + code[3:] if len(code) > 3 else code for code, _ in codes]
    counts = [count for _, count in codes]
    bars = ax.barh(labels[::-1], counts[::-1], color=TEAL, height=.7)
    ax.set_xlim(0, max(counts) * 1.2)
    ax.set_title("E11 subcodes in the selected cohort", loc="left", weight="bold", fontsize=12, pad=12)
    for bar, count in zip(bars, counts[::-1]):
        ax.text(count + 1, bar.get_y() + bar.get_height() / 2, str(count), va="center", fontsize=9)
    ax.grid(axis="x", alpha=.18)
    ax.set_axisbelow(True)

    bmi_outliers = plot_distribution(axes[1, 0], summary["values"]["21001"],
                                     "Body mass index", "kg/m²", (10, 80), BLUE)
    sbp_outliers = plot_distribution(axes[1, 1], summary["values"]["4080"],
                                     "Systolic blood pressure", "mmHg", (60, 250), TEAL)

    ax = axes[2, 0]
    style_axes(ax)
    stages = ["UKB E11 codes", "Paired to a date", "Mapped to OMOP 201826"]
    values = [summary["e11_source_records"], summary["e11_dated_records"], summary["e11_mapped_records"]]
    bars = ax.barh(stages[::-1], values[::-1], color=[TEAL, BLUE, NAVY], height=.55)
    ax.set_xlim(0, max(values) * 1.17)
    ax.set_title("E11 mapping flow", loc="left", weight="bold", fontsize=12, pad=12)
    for bar, value in zip(bars, values[::-1]):
        ax.text(value + 2, bar.get_y() + bar.get_height() / 2, str(value), va="center", fontsize=10)
    ax.grid(axis="x", alpha=.18)
    ax.set_axisbelow(True)

    ax = axes[2, 1]
    style_axes(ax)
    gender = summary["gender_concept_counts"]
    bars = ax.bar(["Female", "Male"], [gender.get("8532", 0), gender.get("8507", 0)],
                  color=[BLUE, TEAL], width=.5)
    ax.set_title("Sex in the E11 cohort", loc="left", weight="bold", fontsize=12, pad=12)
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                str(int(bar.get_height())), ha="center", fontsize=10)
    ax.set_ylim(0, max(bar.get_height() for bar in bars) * 1.17)
    ax.grid(axis="y", alpha=.18)
    ax.set_axisbelow(True)

    fig.text(.12, .025,
             "E11 codes are broadly rolled up to OMOP 201826. QC ranges: BMI 10–80 kg/m²; SBP 60–250 mmHg. "
             "Synthetic values are not clinically validated.",
             fontsize=9, color="#526273")
    return fig, {"bmi_outside_qc_range": bmi_outliers, "sbp_outside_qc_range": sbp_outliers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/ukb_e11_pilot/ukb_subset.tsv"))
    parser.add_argument("--omop", type=Path, default=Path("data/ukb_omop_e11_pilot/all"))
    parser.add_argument("--output", type=Path, default=Path("data/ukb_omop_e11_pilot/qc"))
    args = parser.parse_args()
    summary = summarize(args.source, args.omop)
    figure, extra = build_figure(summary)
    args.output.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "pdf"):
        figure.savefig(args.output / f"ukb_omop_qc.{suffix}", dpi=180, bbox_inches="tight")
    plt.close(figure)
    printable = {key: value for key, value in summary.items() if key != "values"}
    printable.update(extra)
    (args.output / "qc_summary.json").write_text(json.dumps(printable, indent=2) + "\n")
    print(f"Saved {args.output / 'ukb_omop_qc.png'}")
    print(f"Saved {args.output / 'ukb_omop_qc.pdf'}")


if __name__ == "__main__":
    main()
