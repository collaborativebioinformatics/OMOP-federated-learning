#!/usr/bin/env python3
"""Render source-to-OMOP mapping coverage and replay QC for one agent run."""

import argparse
import collections
import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

from mapping_inventory import build_inventory, export as export_inventory


os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "omop_agent_mpl"))
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as error:
    raise SystemExit("matplotlib is required for graphical QC; use a Python with matplotlib installed") from error


NAVY = "#24364b"
TEAL = "#278f86"
BLUE = "#3d76ad"
ORANGE = "#d58a43"
GREY = "#9cabb8"
RED = "#c44747"
BG = "#f5f8fb"
FIELD_LABELS = {"21001": "Body mass index", "4080": "Systolic blood pressure"}
FIELD_QC_RANGES = {"21001": (10, 80), "4080": (60, 250)}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def summarize(run_dir, omop_dir, review_path):
    preflight = json.loads((run_dir / "preflight.json").read_text())
    mapping = json.loads((omop_dir / "mapping_report.json").read_text())
    qc = json.loads((omop_dir / "qc.json").read_text())
    for key in ("source_sha256", "spec_sha256", "vocabulary_release", "diagnosis_selection"):
        if preflight.get(key) != mapping.get(key):
            raise ValueError(f"Preflight and OMOP report disagree on {key}")
    if mapping.get("review_sha256") != digest(review_path):
        raise ValueError("Review file differs from the one used to create this OMOP output")
    rows = read_rows(review_path)
    approved = [row for row in rows if row["decision"].strip().lower() == "approved"]
    demonstration = any("demonstration" in row.get("evidence", "").lower() for row in approved)
    reviewed_fields = {row["field_id"] for row in rows}
    mapped_records = sum(mapping.get("mapped_source_events_by_field", {}).get(field, 0)
                         for field in reviewed_fields)
    mapped_rows = collections.Counter()
    for item in mapping.get("mapped_rows_by_field_domain", []):
        if item["field_id"] in reviewed_fields:
            mapped_rows[item["domain"]] += item["rows"]
    measurements = collections.defaultdict(list)
    for row in read_rows(omop_dir / "measurement.csv"):
        if row["value_as_number"]:
            try:
                measurements[row["measurement_source_value"]].append(float(row["value_as_number"]))
            except ValueError:
                pass
    outliers = {}
    for field, values in measurements.items():
        if field in FIELD_QC_RANGES:
            low, high = FIELD_QC_RANGES[field]
            outliers[field] = sum(value < low or value > high for value in values)
    problems = sum(mapping.get("excluded_events", {}).values()) + sum(
        item["records"] for item in mapping.get("unmapped_units", []))
    return {
        "participants": preflight["people"],
        "selection": preflight["diagnosis_selection"],
        "source_records": preflight["reviewable_records"],
        "dated_records": preflight["dated_reviewable_records"],
        "candidate_records": preflight["dated_records_with_candidates"],
        "mapped_records": mapped_records,
        "distinct_codes": preflight["distinct_review_keys"],
        "approved_codes": len(approved),
        "demonstration": demonstration,
        "top_codes": [(row["source_code"], int(row["dated_records"]),
                       row["decision"].strip().lower() == "approved") for row in rows[:10]],
        "mapped_rows_by_domain": dict(mapped_rows),
        "measurements": measurements,
        "numeric_outliers": outliers,
        "qc_pass": bool(qc.get("pass")),
        "qc_tables": qc.get("tables", {}),
        "exclusion_or_unit_issues": problems,
        "vocabulary_release": preflight["vocabulary_release"],
    }


def _style(ax):
    ax.set_facecolor("white")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(colors=NAVY, labelsize=9)
    ax.grid(axis="x", alpha=.16)
    ax.set_axisbelow(True)


def _barh(ax, labels, counts, colors, title):
    _style(ax)
    ax.set_title(title, loc="left", weight="bold", fontsize=12, color=NAVY, pad=12)
    if not counts or max(counts) == 0:
        ax.set_axis_off()
        ax.text(.5, .5, "No mapped records", ha="center", va="center", transform=ax.transAxes, color=GREY)
        return
    bars = ax.barh(labels[::-1], counts[::-1], color=colors[::-1], height=.66)
    xmax = max(counts)
    ax.set_xlim(0, xmax * 1.24)
    for bar, count in zip(bars, counts[::-1]):
        ax.text(bar.get_width() + xmax * .02,
                bar.get_y() + bar.get_height() / 2, f"{count:,}", va="center", fontsize=9, color=NAVY)


def _hist(ax, values, title, field=None):
    _style(ax)
    ax.set_title(title, loc="left", weight="bold", fontsize=12, color=NAVY, pad=12)
    if not values:
        ax.text(.5, .5, "No numeric measurements", ha="center", va="center", transform=ax.transAxes, color=GREY)
        return
    bounds = FIELD_QC_RANGES.get(field)
    plotted = [value for value in values if bounds[0] <= value <= bounds[1]] if bounds else values
    if not plotted:
        ax.text(.5, .5, "No values in QC range", ha="center", va="center", transform=ax.transAxes, color=GREY)
        return
    ax.hist(plotted, bins=min(24, max(5, int(len(plotted) ** .5))), color=BLUE, edgecolor="white", linewidth=.5)
    ax.set_ylabel("Measurements", fontsize=9)
    note = f"n = {len(values):,}"
    if bounds:
        note += f"\nOutside {bounds[0]}–{bounds[1]}: {len(values) - len(plotted):,}"
    ax.text(.98, .95, note, ha="right", va="top",
            transform=ax.transAxes, fontsize=9, color=NAVY,
            bbox={"boxstyle": "round,pad=.35", "facecolor": "white", "edgecolor": "#dce5ec"})


def figure_for(summary):
    plt.rcParams.update({"font.family": "DejaVu Sans", "savefig.facecolor": BG})
    fig, axes = plt.subplots(3, 2, figsize=(14, 12))
    fig.set_facecolor(BG)
    fig.subplots_adjust(left=.11, right=.95, top=.87, bottom=.09, hspace=.58, wspace=.42)
    selection = summary["selection"]
    chosen = [*selection.get("codes", []), *(prefix + "*" for prefix in selection.get("prefixes", []))]
    scope = ", ".join(chosen) if chosen else "all reviewable codes"
    title = "Source → OMOP | graphical mapping QC"
    if summary["demonstration"]:
        title += " · demonstration"
    fig.suptitle(title, x=.11, y=.97, ha="left",
                 fontsize=21, color=NAVY, weight="bold")
    fig.text(.11, .925,
             f"Selection: {scope}   •   {summary['participants']:,} participants   •   "
             f"{summary['vocabulary_release']}", fontsize=10, color="#556679")

    _barh(axes[0, 0], ["Source records", "Valid paired date", "Athena candidate", "Approved & mapped"],
          [summary["source_records"], summary["dated_records"],
           summary["candidate_records"], summary["mapped_records"]],
          [GREY, BLUE, ORANGE, TEAL], "Selected source record coverage")
    total = summary["dated_records"]
    percentage = 100 * summary["mapped_records"] / total if total else 0
    axes[0, 0].text(0, -0.25, f"{percentage:.1f}% of dated selected records annotated",
                    transform=axes[0, 0].transAxes, fontsize=9, color="#556679")

    domains = summary["mapped_rows_by_domain"]
    order = [domain for domain in ("Condition", "Observation", "Measurement", "Procedure") if domains.get(domain)]
    _barh(axes[0, 1], order, [domains[domain] for domain in order],
          [TEAL, BLUE, ORANGE, GREY][:len(order)], "OMOP rows from approved source records")
    axes[0, 1].text(0, -0.25, "One source record may create multiple OMOP rows.",
                    transform=axes[0, 1].transAxes, fontsize=9, color="#556679")

    top = summary["top_codes"]
    _barh(axes[1, 0], [code for code, _, _ in top], [count for _, count, _ in top],
          [TEAL if approved else ORANGE for _, _, approved in top],
          "Most frequent selected source codes")
    axes[1, 0].text(0, -0.25,
                    f"{summary['approved_codes']:,}/{summary['distinct_codes']:,} distinct codes approved  •  "
                    "teal = approved",
                    transform=axes[1, 0].transAxes, fontsize=9, color="#556679")

    ax = axes[1, 1]
    _style(ax)
    ax.set_title("QC checks", loc="left", weight="bold", fontsize=12, color=NAVY, pad=12)
    ax.set_axis_off()
    checks = [
        ("Replay matches source + review", summary["qc_pass"]),
        ("Unique row IDs", all(item.get("unique_ids") for item in summary["qc_tables"].values())),
        ("No person-key orphans", all(item.get("orphan_rows", 0) == 0 for item in summary["qc_tables"].values())),
        ("No excluded/unknown-unit events", summary["exclusion_or_unit_issues"] == 0),
    ]
    if summary["numeric_outliers"]:
        checks.append(("Values inside configured QC ranges", not any(summary["numeric_outliers"].values())))
    for index, (label, passed) in enumerate(checks):
        y = .89 - index * .18
        ax.text(.02, y, "PASS" if passed else "CHECK", color="white", va="center", fontsize=9,
                weight="bold", bbox={"boxstyle": "round,pad=.35", "facecolor": TEAL if passed else RED,
                                     "edgecolor": "none"})
        ax.text(.22, y, label, va="center", fontsize=10, color=NAVY)

    measurement_fields = sorted(summary["measurements"],
                                key=lambda field: -len(summary["measurements"][field]))[:2]
    for ax, field in zip(axes[2], measurement_fields):
        _hist(ax, summary["measurements"][field], FIELD_LABELS.get(field, f"Measurement {field}"), field)
    for ax in list(axes[2])[len(measurement_fields):]:
        _hist(ax, [], "Measurement distribution")
    fig.text(.11, .025,
             "Coverage measures technical annotation, not clinical correctness. Review target meaning, source provenance, and units separately.",
             fontsize=9, color="#556679")
    return fig


def detail_figure(entries, inventory):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.set_facecolor(BG)
    fig.subplots_adjust(left=.12, right=.95, top=.86, bottom=.09, hspace=.48, wspace=.42)
    title = "What mapped, and what is missing"
    if inventory["demonstration"]:
        title += " · demonstration"
    fig.suptitle(title, x=.12, y=.97, ha="left",
                 fontsize=21, color=NAVY, weight="bold")
    selection = inventory["diagnosis_selection"]
    scope = ", ".join([*selection.get("codes", []),
                       *(prefix + "*" for prefix in selection.get("prefixes", []))]) or "all reviewable codes"
    fig.text(.12, .925,
             f"Selection: {scope}  •  {inventory['mapped_source_records']:,} mapped dated records  •  "
             f"{inventory['unmapped_dated_records']:,} unmapped dated records  •  "
             f"{inventory['undated_source_records']:,} without valid date",
             fontsize=10, color="#556679")

    status_counts = inventory["status_code_counts"]
    names = list(status_counts)
    colors = [TEAL if name == "mapped" else ORANGE if name == "awaiting_review" else RED for name in names]
    _barh(axes[0, 0], [name.replace("_", " ").title() for name in names],
          [status_counts[name] for name in names], colors, "Distinct source codes by status")

    missing_reasons = {key: value for key, value in inventory["status_unmapped_record_counts"].items() if value}
    _barh(axes[0, 1], [name.replace("_", " ").title() for name in missing_reasons],
          list(missing_reasons.values()), [ORANGE if name == "awaiting_review" else RED for name in missing_reasons],
          "Unmapped dated records by reason")
    if not missing_reasons:
        axes[0, 1].texts[-1].set_text("No missing dated records in this selection")

    missing = sorted((item for item in entries if item["unmapped_dated_records"]),
                     key=lambda item: -item["unmapped_dated_records"])[:12]
    _barh(axes[1, 0], [item["source_code"] for item in missing],
          [item["unmapped_dated_records"] for item in missing],
          [ORANGE if item["status"] == "awaiting_review" else RED for item in missing],
          "Top missing source codes")
    if not missing:
        axes[1, 0].texts[-1].set_text("No missing dated records in this selection")

    ax = axes[1, 1]
    _style(ax)
    ax.set_title("Mapped codes and OMOP targets", loc="left", weight="bold", fontsize=12, color=NAVY, pad=12)
    ax.set_axis_off()
    mapped = sorted((item for item in entries if item["mapped_source_records"]),
                    key=lambda item: -item["mapped_source_records"])[:11]
    if mapped:
        ax.text(.02, .94, "Source", fontsize=9, weight="bold", color=NAVY, transform=ax.transAxes)
        ax.text(.25, .94, "Records", fontsize=9, weight="bold", color=NAVY, transform=ax.transAxes)
        ax.text(.44, .94, "Approved OMOP concepts", fontsize=9, weight="bold", color=NAVY, transform=ax.transAxes)
        for index, item in enumerate(mapped):
            y = .86 - index * .07
            ax.text(.02, y, item["source_code"], fontsize=9, color=NAVY, transform=ax.transAxes)
            ax.text(.25, y, str(item["mapped_source_records"]), fontsize=9, color=NAVY, transform=ax.transAxes)
            targets = item["approved_target_ids"].replace(";", ", ")
            ax.text(.44, y, targets, fontsize=9, color=NAVY, transform=ax.transAxes)
    else:
        ax.text(.5, .5, "No approved mappings applied", ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color=GREY)
    fig.text(.12, .025,
             "See mapping_inventory.csv for every source code and missing_codes.csv for records needing attention. Technical coverage is not clinical validation.",
             fontsize=9, color="#556679")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Directory containing preflight.json")
    parser.add_argument("--omop", type=Path, required=True, help="Directory containing OMOP CSVs, mapping_report.json, qc.json")
    parser.add_argument("--review", type=Path, help="Review CSV used for this OMOP output; defaults to run/mapping_review.csv")
    parser.add_argument("--output", type=Path, help="PNG output path; defaults to omop/graphical_qc.png")
    args = parser.parse_args()
    review = args.review or args.run / "mapping_review.csv"
    output = args.output or args.omop / "graphical_qc.png"
    try:
        summary = summarize(args.run, args.omop, review)
        fig = figure_for(summary)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=180, bbox_inches="tight")
        pdf = output.with_suffix(".pdf")
        fig.savefig(pdf, bbox_inches="tight")
        plt.close(fig)
        entries, inventory = build_inventory(args.run, args.omop, review)
        export_inventory(args.run, args.omop, review, args.omop)
        details = detail_figure(entries, inventory)
        detail_png = output.with_name("mapping_detail.png")
        detail_pdf = detail_png.with_suffix(".pdf")
        details.savefig(detail_png, dpi=180, bbox_inches="tight")
        details.savefig(detail_pdf, bbox_inches="tight")
        plt.close(details)
        printable = {key: value for key, value in summary.items() if key != "measurements"}
        output.with_suffix(".json").write_text(json.dumps(printable, indent=2) + "\n")
        print(output)
        print(pdf)
        print(detail_png)
        print(detail_pdf)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
