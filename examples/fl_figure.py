from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).parent
DATA = HERE / "fl_benchmark.json"
OUT = HERE / "fl_benchmark.png"

INK, MUTED, GRID, SURFACE = "#1a1a19", "#5c5c58", "#e4e4e0", "#fcfcfb"
SERIES = {"centralized": "#1baf7a", "federated": "#2a78d6", "local": "#eb6834"}


def thousands(value: float, _: int) -> str:
    return f"{value:,.0f}"


def main() -> None:
    payload = json.loads(DATA.read_text())
    accuracy, throughput = payload["accuracy"], payload["throughput"]
    ns = [row["sites"] for row in accuracy]

    fig, (left, right) = plt.subplots(1, 2, figsize=(13.5, 4.8), facecolor=SURFACE)
    fig.subplots_adjust(wspace=0.42, right=0.9)

    nudge = {"centralized": -10, "federated": 8, "local": 0}
    for name, color in SERIES.items():
        values = [row[name] for row in accuracy]
        left.plot(ns, values, color=color, lw=2, marker="o", ms=6, zorder=3)
        left.annotate(
            name,
            (ns[-1], values[-1]),
            textcoords="offset points",
            xytext=(10, nudge[name]),
            color=MUTED,
            fontsize=10,
            va="center",
        )
    left.set_xscale("log")
    left.set_xticks(ns)
    left.xaxis.set_major_formatter(FuncFormatter(thousands))
    left.set_xticklabels([f"{row['sites']}\n{row['patients_per_site']}/site" for row in accuracy])
    left.set_xlabel("sites the same cohort is split across", color=MUTED, fontsize=10)
    left.set_ylabel("AUROC on a common test set", color=MUTED, fontsize=10)
    left.set_title(
        "Fragmenting a cohort costs a local model\n10 AUROC points; federating recovers them",
        color=INK,
        fontsize=12,
        loc="left",
        pad=12,
    )

    sizes = [row["people"] for row in throughput]
    seconds = [row["seconds"] for row in throughput]
    bars = right.bar([str(s) for s in sizes], seconds, color="#2a78d6", width=0.55, zorder=3)
    for bar, row in zip(bars, throughput, strict=True):
        right.annotate(
            f"{row['seconds']:.2f}s\n{row['people_per_second'] / 1e6:.1f}M/s",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            textcoords="offset points",
            xytext=(0, 6),
            ha="center",
            color=MUTED,
            fontsize=9,
        )
    def rows_label(events: int) -> str:
        return f"{events / 1e6:.0f}M rows" if events >= 1e6 else f"{events // 1000}K rows"

    labels = [f"{s:,}\n{rows_label(row['events'])}" for s, row in zip(sizes, throughput, strict=True)]
    right.set_xticklabels(labels)
    right.set_xlabel("patients in one site", color=MUTED, fontsize=10)
    right.set_ylabel("seconds to build the design matrix", color=MUTED, fontsize=10)
    right.set_ylim(0, max(seconds) * 1.35)
    right.set_title(
        "Design matrix build time stays sub-second\nto 50M rows; the scan never leaves DuckDB",
        color=INK,
        fontsize=12,
        loc="left",
        pad=12,
    )

    for axis in (left, right):
        axis.set_facecolor(SURFACE)
        axis.grid(axis="y", color=GRID, lw=1, zorder=0)
        axis.set_axisbelow(True)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            axis.spines[side].set_color(GRID)
        axis.tick_params(colors=MUTED, labelsize=9)

    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
