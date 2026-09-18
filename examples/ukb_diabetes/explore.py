"""Compare the cohorts the sites bring to the federation, before anything is trained."""

from __future__ import annotations

import argparse
from pathlib import Path

import ehrapy as ep
import ehrdata
import matplotlib
import numpy as np
import pandas as pd
from ehrdata import EHRData

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cohort import CANDIDATES, LOOKBACK_DAYS, VOCABULARY_VERSION, index_table, site_paths

import omopflare as of

HERE = Path(__file__).parent
SITE, ACCENT, CASE, MUTED = "#94a3b8", "#16a34a", "#b91c1c", "#64748b"
DIVERGING = "RdBu_r"


def build(paths: tuple[Path, ...], spec: of.FeatureSpec) -> tuple[EHRData, dict[str, int]]:
    """Read every site into one cohort and count who each rule removes.

    Args:
        paths: One directory per site.
        spec: The agreed feature schema.

    Returns:
        People by feature with the site and the label in ``obs``, and the funnel counts.
    """
    names = [feature.name for feature in spec.features]
    blocks, frames = [], []
    registered = eligible = 0
    for path in paths:
        source = of.OmopSource(path)
        registered += source.connection.execute("select count(*) from person").fetchone()[0]
        index = index_table(source)
        eligible += index.num_rows
        person_ids, features = of.feature_matrix(source, spec, index)
        labels = dict(
            zip(
                np.asarray(index.column("person_id")).tolist(),
                np.asarray(index.column("label")).tolist(),
                strict=True,
            )
        )
        blocks.append(features[:, : len(names)])
        frames.append(
            pd.DataFrame(
                {"site": path.name, "label": [labels[int(person)] for person in person_ids]},
                index=[f"{path.name}:{person}" for person in person_ids],
            )
        )
    observations = pd.concat(frames)
    edata = EHRData(np.vstack(blocks))
    edata.var_names = names
    edata.obs = observations
    edata.obs["site"] = pd.Categorical(edata.obs["site"])
    edata.obs["case"] = pd.Categorical(np.where(edata.obs["label"].to_numpy() > 0, "case", "control"))
    ehrdata.infer_feature_types(edata, output=None)
    funnel = {
        "registered": registered,
        "no diabetes yet": eligible,
        "followed past\nthe landmark": edata.n_obs,
        "incident cases": int(edata.obs["label"].sum()),
    }
    return edata, funnel


def raw_values(paths: tuple[Path, ...], feature: of.Feature) -> np.ndarray:
    """Read one concept's values before the plausible range is applied.

    Args:
        paths: One directory per site.
        feature: The numeric feature to read.

    Returns:
        Every recorded value for that concept, across sites.
    """
    values = []
    for path in paths:
        values.append(
            of.OmopSource(path)
            .connection.execute(
                "select try_cast(value_as_number as double) from measurement "
                f"where measurement_concept_id = {feature.concept_id} and value_as_number is not null"
            )
            .df()
            .iloc[:, 0]
            .to_numpy()
        )
    return np.concatenate(values)


def standardised_difference(edata: EHRData) -> pd.DataFrame:
    """Measure how far each site's feature means sit from the pooled mean.

    Args:
        edata: The stacked cohort.

    Returns:
        Sites by features, in pooled standard deviations.
    """
    frame = pd.DataFrame(edata.X, columns=list(edata.var_names))
    frame["site"] = edata.obs["site"].to_numpy()
    pooled_mean = frame[list(edata.var_names)].mean()
    pooled_std = frame[list(edata.var_names)].std().replace(0.0, np.nan)
    by_site = frame.groupby("site", observed=True)[list(edata.var_names)].mean()
    return (by_site - pooled_mean) / pooled_std


def funnel(axis: plt.Axes, stages: dict[str, int]) -> None:
    """Draw how the cohort narrows.

    Args:
        axis: Axes to draw on.
        stages: Counts per stage.
    """
    names, values = list(stages), list(stages.values())
    axis.barh(range(len(names)), values, color=[SITE] * (len(names) - 1) + [CASE])
    axis.set_yticks(range(len(names)))
    axis.set_yticklabels(names, fontsize=8)
    axis.invert_yaxis()
    axis.set_xscale("log")
    axis.set_xlabel("people (log scale)", fontsize=9)
    for position, value in enumerate(values):
        axis.text(value * 1.2, position, f"{value:,}", va="center", fontsize=7, color=MUTED)
    axis.set_xlim(right=max(values) * 6)
    axis.set_title("Who is left to train on", fontsize=10)


def case_rate(axis: plt.Axes, edata: EHRData) -> None:
    """Plot each site's case rate against the pooled rate.

    Args:
        axis: Axes to draw on.
        edata: The stacked cohort.
    """
    grouped = edata.obs.groupby("site", observed=True)["label"]
    rates, sizes = grouped.mean() * 100, grouped.size()
    pooled = float(edata.obs["label"].mean()) * 100
    positions = np.arange(len(rates))
    axis.hlines(positions, pooled, rates, color=SITE, linewidth=1)
    axis.scatter(rates, positions, s=np.sqrt(sizes) * 2.5, color=CASE, zorder=3)
    axis.axvline(pooled, color=MUTED, linewidth=1)
    axis.text(pooled, -0.75, f" pooled {pooled:.2f}%", fontsize=7, color=MUTED, va="bottom")
    axis.set_yticks(positions)
    axis.set_yticklabels([name.replace("centre_", "") for name in rates.index], fontsize=7)
    axis.set_ylim(len(rates) - 0.5, -1.0)
    axis.set_xlabel("incident cases (%)", fontsize=9)
    axis.set_xlim(0, max(rates.max() * 1.25, pooled * 2))
    axis.set_title("Case rate by site", fontsize=10)


def divergence(axis: plt.Axes, differences: pd.DataFrame, *, band: float = 0.1) -> None:
    """Plot how far each site's feature means sit from the pooled mean.

    Args:
        axis: Axes to draw on.
        differences: Sites by features, in pooled standard deviations.
        band: Half-width of the region where sites are treated as indistinguishable.
    """
    features = list(differences.columns)
    axis.axvline(0.0, color=MUTED, linewidth=1)
    for edge in (-band, band):
        axis.axvline(edge, color=MUTED, linewidth=0.8, linestyle=":")
    for position, feature in enumerate(features):
        values = differences[feature].to_numpy()
        axis.scatter(values, np.full(values.size, position), s=28, color=SITE, edgecolor="white", linewidth=0.5)
    reach = float(np.nanmax(np.abs(differences.to_numpy())))
    axis.set_yticks(range(len(features)))
    axis.set_yticklabels(features, fontsize=8)
    axis.set_ylim(-0.6, len(features) - 0.4)
    axis.set_xlim(-max(reach * 1.3, band * 2), max(reach * 1.3, band * 2))
    axis.set_xlabel("site mean minus pooled mean (SD)", fontsize=9)
    axis.text(
        0.98, 0.06, f"furthest site {reach:.2f} SD", transform=axis.transAxes, ha="right", fontsize=7, color=MUTED
    )
    axis.set_title("How far the sites sit apart", fontsize=10)


def plausibility(axis: plt.Axes, values: np.ndarray, feature: of.Feature) -> None:
    """Show recorded values against the range the spec accepts.

    Args:
        axis: Axes to draw on.
        values: Every recorded value for the concept.
        feature: The feature, for its plausible range.
    """
    low, high = feature.plausible_range
    positive = values[np.isfinite(values) & (values > 0)]
    axis.hist(positive, bins=np.logspace(np.log10(positive.min()), np.log10(positive.max()), 60), color=SITE)
    axis.axvspan(low, high, color=ACCENT, alpha=0.15)
    for edge in (low, high):
        axis.axvline(edge, color=ACCENT, linewidth=1)
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel(f"{feature.name}, as recorded", fontsize=9)
    axis.set_ylabel("readings (log scale)", fontsize=9)
    kept = float(((positive >= low) & (positive <= high)).mean())
    axis.text(0.98, 0.92, f"the range keeps {kept:.0%}", transform=axis.transAxes, ha="right", fontsize=7, color=MUTED)
    axis.set_title(f"{feature.name} against the plausible range", fontsize=10)


def separation(axis: plt.Axes, edata: EHRData, feature: str) -> None:
    """Overlay one feature for the cases and the controls.

    Args:
        axis: Axes to draw on.
        edata: The stacked cohort.
        feature: Feature to compare.
    """
    values = edata[:, feature].X.ravel()
    case = (edata.obs["case"] == "case").to_numpy()
    observed = ~np.isnan(values)
    bins = np.linspace(np.nanmin(values), np.nanmax(values), 30)
    axis.hist(
        values[observed & ~case], bins=bins, density=True, color=SITE, label=f"control ({(observed & ~case).sum():,})"
    )
    axis.hist(
        values[observed & case],
        bins=bins,
        density=True,
        histtype="step",
        linewidth=1.6,
        color=CASE,
        label=f"case ({(observed & case).sum():,})",
    )
    axis.set_xlabel(feature, fontsize=9)
    axis.set_ylabel("density", fontsize=9)
    axis.legend(fontsize=7, frameon=False)
    difference = float(np.nanmean(values[case]) - np.nanmean(values[~case])) / float(np.nanstd(values))
    axis.text(0.98, 0.72, f"cases {difference:+.2f} SD", transform=axis.transAxes, ha="right", fontsize=7, color=MUTED)
    axis.set_title(f"{feature}, cases and controls", fontsize=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", type=Path, default=HERE / "data" / "omop")
    parser.add_argument("--out", type=Path, default=HERE / "cohort_overview_ukb.png")
    parser.add_argument("--title", default="UK Biobank synthetic extract, before any modelling")
    args = parser.parse_args()

    paths = site_paths(args.sites)
    spec = of.FeatureSpec(CANDIDATES, VOCABULARY_VERSION, LOOKBACK_DAYS)
    edata, stages = build(paths, spec)
    print(ep.pp.qc_metrics(edata)[1][["missing_values_pct", "median", "min", "max"]].round(2).to_string())
    print(" -> ".join(f"{name.replace(chr(10), ' ')} {value:,}" for name, value in stages.items()))
    differences = standardised_difference(edata)
    print(differences.round(3).to_string())

    first, second = spec.features[0], spec.features[1]
    figure, axes = plt.subplots(2, 3, figsize=(16, 8.0))
    funnel(axes[0, 0], stages)
    case_rate(axes[0, 1], edata)
    divergence(axes[0, 2], differences)
    plausibility(axes[1, 0], raw_values(paths, second), second)
    separation(axes[1, 1], edata, first.name)
    separation(axes[1, 2], edata, second.name)
    for axis in axes.ravel():
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(args.title, fontsize=12)
    figure.tight_layout()
    figure.savefig(args.out, dpi=170)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
