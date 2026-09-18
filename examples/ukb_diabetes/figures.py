"""Draw what the federated run showed, from the JSON that run.py wrote."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
LOCAL, FEDERATED, POOLED, MUTED = "#94a3b8", "#16a34a", "#1e40af", "#64748b"


def _site_names(results: dict) -> list[str]:
    return list(results["sites"])


def heterogeneity(axis: plt.Axes, results: dict) -> None:
    """Show how much training data and how many cases each site holds.

    Args:
        axis: Axes to draw on.
        results: The parsed results file.
    """
    names = _site_names(results)
    sizes = [results["sites"][name]["train"] for name in names]
    incidence = [100 * results["incidence"][name] for name in names]
    axis.bar(range(len(names)), sizes, color=LOCAL)
    axis.set_ylabel("training people", color=MUTED, fontsize=9)
    axis.set_xticks(range(len(names)))
    axis.set_xticklabels([name.replace("centre_", "") for name in names], rotation=45, ha="right", fontsize=7)
    twin = axis.twinx()
    twin.plot(range(len(names)), incidence, "o-", color=FEDERATED, markersize=4, linewidth=1.2)
    twin.set_ylabel("incident cases (%)", color=FEDERATED, fontsize=9)
    twin.set_ylim(bottom=0)
    axis.set_title("What each site holds", fontsize=10)


def generalisation(axis: plt.Axes, results: dict) -> None:
    """Compare every model on the one held-out cohort pooled across all sites.

    Args:
        axis: Axes to draw on.
        results: The parsed results file.
    """
    names = _site_names(results)
    local = [results["pooled_test"][name] for name in names]
    order = [*names, "federated", "pooled"]
    scores = [results["pooled_test"][name] for name in order]
    bounds = np.array([results["pooled_test_ci"][name] for name in order]).T
    errors = np.abs(bounds - np.array(scores))
    colours = [LOCAL] * len(names) + [FEDERATED, POOLED]
    axis.bar(range(len(order)), scores, color=colours)
    axis.errorbar(range(len(order)), scores, yerr=errors, fmt="none", ecolor=MUTED, elinewidth=1, capsize=2)
    for colour, label in ((LOCAL, "one site only"), (FEDERATED, "federated"), (POOLED, "pooled (upper bound)")):
        axis.bar(0, 0, color=colour, label=label)
    axis.axhline(0.5, color=MUTED, linestyle=":", linewidth=1)
    axis.set_xticks(range(len(order)))
    axis.set_xticklabels(
        [name.replace("centre_", "") for name in names] + ["fed", "pool"], rotation=45, ha="right", fontsize=7
    )
    axis.set_ylabel("AUROC on the pooled held-out cohort", fontsize=9)
    axis.set_ylim(0.0, 1.0)
    axis.legend(fontsize=7, frameon=False, loc="upper left")
    axis.set_xlim(-0.8, len(order) - 0.2)
    spread = np.nanstd(local)
    axis.set_title(f"Generalisation (local spread {spread:.3f})", fontsize=10)


def coefficients(axis: plt.Axes, results: dict) -> None:
    """Show how far single-site coefficients scatter around the federated fit.

    Args:
        axis: Axes to draw on.
        results: The parsed results file.
    """
    names = _site_names(results)
    features = results["features"]
    local = np.array([results["coefficients"][name] for name in names])
    positions = np.arange(len(features))
    for row in local:
        axis.plot(positions, row, "o", color=LOCAL, markersize=4, alpha=0.7)
    axis.plot(positions, results["coefficients"]["federated"], "D", color=FEDERATED, markersize=7, label="federated")
    axis.plot(positions, results["coefficients"]["pooled"], "_", color=POOLED, markersize=18, label="pooled")
    axis.axhline(0.0, color=MUTED, linestyle=":", linewidth=1)
    axis.set_xticks(positions)
    axis.set_xticklabels(features, rotation=45, ha="right", fontsize=7)
    axis.set_ylabel("coefficient", fontsize=9)
    axis.legend(fontsize=7, frameon=False)
    axis.set_title("Single-site estimates scatter", fontsize=10)


def draw(results: dict, out: Path, title: str) -> None:
    """Write the three-panel summary.

    Args:
        results: The parsed results file.
        out: Where to write the PNG.
        title: Figure title.
    """
    figure, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))
    heterogeneity(axes[0], results)
    generalisation(axes[1], results)
    coefficients(axes[2], results)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(out, dpi=200)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=HERE / "results_ukb.json")
    parser.add_argument("--out", type=Path, default=HERE / "federated_vs_local_ukb.png")
    parser.add_argument("--title", default="Incident type 2 diabetes across UK Biobank assessment centres")
    args = parser.parse_args()
    draw(json.loads(args.results.read_text()), args.out, args.title)


if __name__ == "__main__":
    main()
