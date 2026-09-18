"""Look at the mapped OMOP cohort with ehrapy before anything is trained on it.

Every site's feature matrix is stacked into one EHRData, which is what the sites could never
actually do; it is built here only to show what the federation is working with.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anndata as ad
import ehrapy as ep
import ehrdata
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from cohort import CANDIDATES, LOOKBACK_DAYS, VOCABULARY_VERSION, index_table, site_paths

import omopflare as of

HERE = Path(__file__).parent


def build(paths: tuple[Path, ...], spec: of.FeatureSpec) -> ad.AnnData:
    """Stack every site's feature matrix into one annotated cohort.

    Args:
        paths: One directory per site.
        spec: The agreed feature schema.

    Returns:
        People by features, with the site and the label in ``obs``.
    """
    blocks, observations = [], []
    for path in paths:
        source = of.OmopSource(path)
        index = index_table(source)
        person_ids, features = of.feature_matrix(source, spec, index)
        labels = dict(
            zip(
                np.asarray(index.column("person_id")).tolist(),
                np.asarray(index.column("label")).tolist(),
                strict=True,
            )
        )
        blocks.append(features)
        observations.append(
            pd.DataFrame(
                {
                    "site": path.name,
                    "incident_diabetes": [labels[int(person)] for person in person_ids],
                },
                index=[f"{path.name}:{person}" for person in person_ids],
            )
        )
    adata = ad.AnnData(np.vstack(blocks), obs=pd.concat(observations))
    adata.var_names = list(spec.column_names)
    adata.obs["incident_diabetes"] = adata.obs["incident_diabetes"].map({0.0: "no", 1.0: "yes"}).astype("category")
    adata.obs["site"] = adata.obs["site"].astype("category")
    return adata


def between_site_variance(values: np.ndarray, sites: np.ndarray) -> float:
    """Share of a component's variance that lies between sites rather than within them.

    Args:
        values: One coordinate per person.
        sites: The site each person belongs to.

    Returns:
        Eta squared, 0 when the sites are indistinguishable.
    """
    grand = values.mean()
    between = sum(((values[sites == site].mean() - grand) ** 2) * (sites == site).sum() for site in np.unique(sites))
    return float(between / ((values - grand) ** 2).sum())


def draw(adata: ad.AnnData, out: Path, title: str) -> None:
    """Write the cohort overview.

    Args:
        adata: The stacked cohort.
        out: Where to write the PNG.
        title: Figure title.
    """
    numeric = [name for name in adata.var_names if not name.endswith("_missing")]
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.2))

    missing = adata[:, [f"{name}_missing" for name in numeric]].X.mean(axis=0) * 100
    axes[0].bar(numeric, missing, color="#94a3b8")
    axes[0].set_ylabel("missing at the landmark (%)", fontsize=9)
    axes[0].set_title("Coverage after mapping", fontsize=10)

    frame = pd.DataFrame(adata.X, columns=list(adata.var_names))
    frame["site"] = adata.obs["site"].to_numpy()
    frame.boxplot(column=numeric[0], by="site", ax=axes[1], grid=False)
    axes[1].set_title(f"{numeric[0]} by site", fontsize=10)
    axes[1].set_xlabel("")
    axes[1].tick_params(axis="x", rotation=45, labelsize=7)
    figure.suptitle("")

    ehrdata.infer_feature_types(adata, output=None)
    ep.pp.knn_impute(adata, var_names=numeric)
    ep.pp.scale_norm(adata)
    ep.pp.pca(adata, n_comps=2)
    coordinates = adata.obsm["X_pca"]
    sites = adata.obs["site"].to_numpy()
    for site in adata.obs["site"].cat.categories:
        mask = sites == site
        axes[2].scatter(coordinates[mask, 0], coordinates[mask, 1], s=2, alpha=0.3, label=site)
    axes[2].set_xlabel("PC1", fontsize=9)
    axes[2].set_ylabel("PC2", fontsize=9)
    axes[2].legend(fontsize=6, frameon=False, markerscale=4)
    axes[2].set_title(f"Site explains {between_site_variance(coordinates[:, 0], sites):.1%} of PC1", fontsize=10)

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(out, dpi=200)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", type=Path, default=HERE / "data" / "omop")
    parser.add_argument("--out", type=Path, default=HERE / "cohort_overview_ukb.png")
    parser.add_argument("--title", default="UK Biobank synthetic cohort after OMOP mapping")
    args = parser.parse_args()

    paths = site_paths(args.sites)
    spec = of.FeatureSpec(CANDIDATES, VOCABULARY_VERSION, LOOKBACK_DAYS, missing_indicators=True)
    adata = build(paths, spec)
    print(f"{adata.n_obs:,} people, {adata.n_vars} features, {adata.obs['site'].nunique()} sites")
    print(adata.obs.groupby("site", observed=True)["incident_diabetes"].value_counts().unstack().to_string())
    draw(adata, args.out, args.title)


if __name__ == "__main__":
    main()
