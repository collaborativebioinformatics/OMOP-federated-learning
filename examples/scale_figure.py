from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent
DATA = HERE / "scale_benchmark.json"
OUT = HERE / "scale_benchmark.png"

INK, MUTED, GRID, SURFACE = "#1a1a19", "#5c5c58", "#e4e4e0", "#fcfcfb"
STAGES = {
    "build_seconds": ("build design matrix", "#2a78d6"),
    "scan_seconds": ("duckdb scan", "#eb6834"),
    "epoch_seconds": ("training epoch", "#1baf7a"),
}


def main() -> None:
    rows = json.loads(DATA.read_text())
    people = [row["people"] for row in rows]

    fig, (left, right) = plt.subplots(1, 2, figsize=(14, 4.8), facecolor=SURFACE)
    fig.subplots_adjust(wspace=0.46, right=0.89)

    nudge = {"build_seconds": 9, "scan_seconds": -9, "epoch_seconds": 0}
    for key, (label, colour) in STAGES.items():
        seconds = [row[key] for row in rows]
        left.plot(people, seconds, color=colour, lw=2, marker="o", ms=6, zorder=3)
        left.annotate(
            label,
            (people[-1], seconds[-1]),
            textcoords="offset points",
            xytext=(10, nudge[key]),
            color=MUTED,
            fontsize=10,
            va="center",
        )
    left.set_xscale("log")
    left.set_yscale("log")
    left.set_xticks(people)
    left.set_xticklabels([f"{p // 1000}k" if p < 1_000_000 else "1M" for p in people])
    left.set_xlabel("patients in one site", color=MUTED, fontsize=10)
    left.set_ylabel("seconds", color=MUTED, fontsize=10)
    left.set_title(
        "Ten times the data costs ten times the time\nabove 10k patients; below it, fixed cost dominates",
        color=INK,
        fontsize=12,
        loc="left",
        pad=12,
    )

    memory = [row["peak_gb"] for row in rows]
    bars = right.bar([str(p) for p in people], memory, color="#2a78d6", width=0.55, zorder=3)
    for bar, row in zip(bars, rows, strict=True):
        right.annotate(
            f"{row['peak_gb']:.1f} GB\n{row['measurement_rows'] / 1e6:,.0f}M rows",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            textcoords="offset points",
            xytext=(0, 6),
            ha="center",
            color=MUTED,
            fontsize=9,
        )
    right.set_xticklabels([f"{p // 1000}k" if p < 1_000_000 else "1M" for p in people])
    right.set_xlabel("patients in one site", color=MUTED, fontsize=10)
    right.set_ylabel("peak resident memory (GB)", color=MUTED, fontsize=10)
    right.set_ylim(0, max(memory) * 1.35)
    right.set_title(
        "1000x the rows costs 6x the memory,\nsince the raw rows are never materialised",
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

    federated = HERE / "scale_federated" / "federated_timing.json"
    if federated.exists():
        run = json.loads(federated.read_text())
        fig.text(
            0.5,
            -0.04,
            f"A real NVFlare FedAvg job over the same {run['people']:,} patients and "
            f"{run['measurement_rows'] / 1e9:.2f}B rows, split across {run['sites']} clients: "
            f"{run['wall_seconds']:.0f}s for {run['rounds']} rounds, "
            f"{run['seconds_per_round']:.1f}s per round.",
            ha="center",
            color=MUTED,
            fontsize=10,
        )

    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
