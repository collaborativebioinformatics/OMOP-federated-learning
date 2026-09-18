#!/usr/bin/env python3
"""Plot distributions of the eight requested variables and observation-period QC."""

import argparse
import collections
import datetime as dt
import json
import math
import statistics
from pathlib import Path

from disease_qc import BG, GREY, NAVY, TEAL, disease_for_code, plt, quantile, rows, write_csv
from summarize import ASSAYS, DISEASES


BLUE = "#3d76ad"
ORANGE = "#d58a43"
PERIOD_BINS = ("1 day", "2–30 days", "31–180 days", "181–365 days", "1–5 years", ">5 years")


def period_bucket(days):
    if days <= 1:
        return PERIOD_BINS[0]
    if days <= 30:
        return PERIOD_BINS[1]
    if days <= 180:
        return PERIOD_BINS[2]
    if days <= 365:
        return PERIOD_BINS[3]
    if days <= 365 * 5:
        return PERIOD_BINS[4]
    return PERIOD_BINS[5]


def collect(omop):
    people = {row["person_id"] for row in rows(omop / "person.csv")}
    groups = {name: set() for name in DISEASES}
    for row in rows(omop / "condition_occurrence.csv"):
        for name in disease_for_code(row["condition_source_value"]):
            groups[name].add(row["person_id"])
    field_values = collections.defaultdict(list)
    group_values = collections.defaultdict(list)
    negative_counts = collections.Counter()
    event_dates = []
    field_names = {field: name for name, field in ASSAYS.items()}
    for row in rows(omop / "measurement.csv"):
        event_dates.append((row["person_id"], row["measurement_date"]))
        field = row["measurement_source_value"]
        if field not in field_names:
            continue
        try:
            value = float(row["value_as_number"])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        field_values[field].append(value)
        if value < 0:
            negative_counts[field] += 1
        for name, members in groups.items():
            if row["person_id"] in members:
                group_values[(name, field)].append(value)
    for row in rows(omop / "condition_occurrence.csv"):
        event_dates.append((row["person_id"], row["condition_start_date"]))

    periods = collections.defaultdict(list)
    period_types = collections.Counter()
    invalid_dates = 0
    reversed_dates = 0
    orphan_periods = 0
    durations = []
    for row in rows(omop / "observation_period.csv"):
        person = row["person_id"]
        period_types[row["period_type_concept_id"]] += 1
        if person not in people:
            orphan_periods += 1
        try:
            start = dt.date.fromisoformat(row["observation_period_start_date"])
            end = dt.date.fromisoformat(row["observation_period_end_date"])
        except ValueError:
            invalid_dates += 1
            continue
        if end < start:
            reversed_dates += 1
            continue
        periods[person].append((start, end))
        durations.append((person, (end - start).days + 1))
    outside_events = 0
    invalid_event_dates = 0
    for person, raw_date in event_dates:
        try:
            date = dt.date.fromisoformat(raw_date)
        except ValueError:
            invalid_event_dates += 1
            continue
        if not any(start <= date <= end for start, end in periods.get(person, ())):
            outside_events += 1
    duration_summary = []
    for name, members in (("All participants", people), *groups.items()):
        values = [days for person, days in durations if person in members]
        duration_summary.append({"group": name, "participants": len(members),
                                 "periods": len(values),
                                 "one_day_periods": sum(days == 1 for days in values),
                                 "median_days": statistics.median(values) if values else None,
                                 "p25_days": quantile(values, .25),
                                 "p75_days": quantile(values, .75),
                                 "max_days": max(values) if values else None})
    checks = {"period_type_counts": dict(period_types),
              "persons_without_period": len(people - set(periods)),
              "orphan_periods": orphan_periods,
              "invalid_period_dates": invalid_dates,
              "reversed_period_dates": reversed_dates,
              "invalid_event_dates": invalid_event_dates,
              "events_outside_period": outside_events,
              "event_rows_checked": len(event_dates)}
    return people, groups, field_values, group_values, negative_counts, durations, duration_summary, checks


def histogram(ax, values, title, negative_count=0, color=BLUE, xlim=None):
    ax.set_facecolor("white")
    ax.set_title(title, loc="left", color=NAVY, fontsize=11, fontweight="bold")
    if values:
        ax.hist(values, bins=32, color=color, edgecolor="white", linewidth=.35)
        median = statistics.median(values)
        ax.axvline(median, color=NAVY, linewidth=1.5, linestyle="--")
        ax.text(.97, .95, f"n={len(values):,}\nmedian={median:.2f}\nnegative={negative_count:,}",
                transform=ax.transAxes, ha="right", va="top", fontsize=8, color=NAVY,
                bbox={"facecolor": "white", "edgecolor": "#dce5ec", "boxstyle": "round,pad=.3"})
    else:
        ax.text(.5, .5, "No values", transform=ax.transAxes, ha="center", va="center", color=GREY)
    if xlim:
        ax.set_xlim(xlim)
    ax.set_xlabel("mmol/L", fontsize=9)
    ax.set_ylabel("Measurements", fontsize=9)
    ax.grid(axis="y", alpha=.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)


def plot_overview(output, people, groups, field_values, negative_counts):
    fig, axes = plt.subplots(3, 3, figsize=(15, 13), layout="constrained")
    fig.set_facecolor(BG)
    fig.suptitle("UKB synthetic data | distributions of eight requested variables",
                 fontsize=18, fontweight="bold", color=NAVY)
    for ax, (name, members) in zip(axes.flat[:3], groups.items()):
        total = len(people)
        values = [total - len(members), len(members)]
        bars = ax.bar(["No selected code", "Ever coded"], values, color=["#aebbc7", TEAL])
        ax.set_title(f"{name}: {', '.join(DISEASES[name])}", loc="left", color=NAVY,
                     fontsize=11, fontweight="bold")
        ax.set_ylabel("Participants", fontsize=9)
        ax.bar_label(bars, fmt="%d", padding=3, fontsize=8)
        ax.set_ylim(0, total * 1.12)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="x", labelsize=8)
    axes.flat[8].set_axis_off()
    axes.flat[8].text(.02, .92,
                      f"{len(people):,} participants\n\n"
                      "Disease bars use selected hospital ICD-10 codes.\n"
                      "Groups may overlap.\n\n"
                      "Lab histograms include repeat assessments.\n"
                      "Dashed line = median.\n"
                      "Negative values are synthetic-data QC findings.",
                      transform=axes.flat[8].transAxes, va="top", color=NAVY,
                      fontsize=10, linespacing=1.5)
    for ax, (name, field) in zip(axes.flat[3:8], ASSAYS.items()):
        histogram(ax, field_values[field], name, negative_counts[field])
    fig.savefig(output / "all_variable_distributions.png", dpi=170)
    fig.savefig(output / "all_variable_distributions.pdf")
    plt.close(fig)


def plot_disease_distributions(output, groups, field_values, group_values):
    for name, members in groups.items():
        fig, axes = plt.subplots(3, 2, figsize=(12, 11), layout="constrained")
        fig.set_facecolor(BG)
        fig.suptitle(f"{name} | distributions of five laboratory variables",
                     fontsize=18, fontweight="bold", color=NAVY)
        for ax, (label, field) in zip(axes.flat, ASSAYS.items()):
            all_values = field_values[field]
            group = group_values[(name, field)]
            negative = sum(value < 0 for value in group)
            xlim = (min(all_values), max(all_values)) if all_values else None
            histogram(ax, group, label, negative, color=TEAL, xlim=xlim)
        axes.flat[-1].set_axis_off()
        axes.flat[-1].text(.03, .92,
                           f"{len(members)} people ever coded with {name}.\n\n"
                           "Each histogram includes all their available\n"
                           "assessment values, including repeat visits.\n"
                           "The x-axis is shared with the whole-cohort\n"
                           "distribution for the same lab.\n\n"
                           "Synthetic values are not clinical findings.",
                           transform=axes.flat[-1].transAxes, va="top",
                           fontsize=10, linespacing=1.5, color=NAVY)
        fig.savefig(output / f"{name}_distributions.png", dpi=170)
        fig.savefig(output / f"{name}_distributions.pdf")
        plt.close(fig)


def plot_periods(output, durations, summary, checks):
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), layout="constrained")
    fig.set_facecolor(BG)
    fig.suptitle("Observation period QC | inferred event spans",
                 fontsize=19, fontweight="bold", color=NAVY)
    all_days = [days for _, days in durations]
    one_day = sum(days == 1 for days in all_days)
    ax = axes[0, 0]
    bars = ax.bar(["One day", "More than one day"],
                  [one_day, len(all_days) - one_day], color=[ORANGE, TEAL])
    ax.bar_label(bars, fmt="%d", padding=4)
    ax.set_title("Period lengths: whole cohort", loc="left", color=NAVY, fontweight="bold")
    ax.set_ylabel("Observation periods")
    ax.spines[["top", "right"]].set_visible(False)
    ax = axes[0, 1]
    bucket_counts = collections.Counter(period_bucket(days) for days in all_days)
    bars = ax.barh(PERIOD_BINS[::-1], [bucket_counts[key] for key in PERIOD_BINS[::-1]], color=BLUE)
    ax.bar_label(bars, padding=4, fontsize=8)
    ax.set_xlim(0, max(bucket_counts.values()) * 1.13 if bucket_counts else 1)
    ax.set_title("Duration categories", loc="left", color=NAVY, fontweight="bold")
    ax.set_xlabel("Periods")
    ax.spines[["top", "right"]].set_visible(False)
    ax = axes[1, 0]
    labels = [item["group"] for item in summary]
    values = [100 * item["one_day_periods"] / item["periods"] if item["periods"] else 0
              for item in summary]
    bars = ax.barh(labels[::-1], values[::-1], color=[TEAL, TEAL, TEAL, ORANGE])
    for bar, item in zip(bars, summary[::-1]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{item['one_day_periods']:,}/{item['periods']:,}", va="center", fontsize=8)
    ax.set_xlim(0, 115)
    ax.set_title("One-day share by group", loc="left", color=NAVY, fontweight="bold")
    ax.set_xlabel("Percent of periods")
    ax.spines[["top", "right"]].set_visible(False)
    ax = axes[1, 1]
    ax.set_axis_off()
    type_text = ", ".join(f"{key}: {value:,}" for key, value in checks["period_type_counts"].items())
    lines = [f"Type concept IDs: {type_text}",
             f"People without a period: {checks['persons_without_period']:,}",
             f"Invalid/reversed dates: {checks['invalid_period_dates'] + checks['reversed_period_dates']:,}",
             f"Events outside periods: {checks['events_outside_period']:,}/{checks['event_rows_checked']:,}",
             "", "Periods span first to last selected dated event.",
             "They are not verified UKB follow-up or person-time."]
    ax.text(.02, .93, "\n".join(lines), transform=ax.transAxes,
            va="top", color=NAVY, fontsize=10, linespacing=1.6)
    fig.savefig(output / "observation_period_qc.png", dpi=170)
    fig.savefig(output / "observation_period_qc.pdf")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    omop = args.run / "omop"
    output = args.run / "visual_qc"
    output.mkdir(exist_ok=True)
    people, groups, values, group_values, negatives, durations, period_summary, checks = collect(omop)
    distribution = []
    for name, members in groups.items():
        distribution.append({"variable": name, "kind": "selected_diagnosis_binary",
                             "source_field": "41270", "people": len(people),
                             "records": "", "coded_people": len(members),
                             "median_mmol_l": "", "p25_mmol_l": "", "p75_mmol_l": "",
                             "negative_values": ""})
    for name, field in ASSAYS.items():
        bucket = values[field]
        distribution.append({"variable": name, "kind": "numeric_measurement",
                             "source_field": field, "people": "", "records": len(bucket),
                             "coded_people": "", "median_mmol_l": statistics.median(bucket),
                             "p25_mmol_l": quantile(bucket, .25),
                             "p75_mmol_l": quantile(bucket, .75),
                             "negative_values": negatives[field]})
    write_csv(output / "variable_distribution_summary.csv", distribution)
    write_csv(output / "observation_period_summary.csv", period_summary)
    (output / "observation_period_checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    plot_overview(output, people, groups, values, negatives)
    plot_disease_distributions(output, groups, values, group_values)
    plot_periods(output, durations, period_summary, checks)
    print(json.dumps({"output": str(output), "people": len(people),
                      "diseases": {name: len(group) for name, group in groups.items()},
                      "negative_values": dict(negatives),
                      "observation_period_checks": checks}, indent=2))


if __name__ == "__main__":
    main()
