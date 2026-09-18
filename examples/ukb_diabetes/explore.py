"""Look at the UK Biobank extract before anything is trained on it."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
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
SAMPLED = 100_000
LOCAL, ACCENT, CASE, MUTED = "#94a3b8", "#16a34a", "#b91c1c", "#64748b"
SBP_RANGE = next(feature.plausible_range for feature in CANDIDATES if feature.name == "sbp")


def person_table(raw: Path) -> duckdb.DuckDBPyConnection:
    """Read one row per participant across every centre.

    Args:
        raw: Directory holding one folder per centre.

    Returns:
        A connection holding a ``people`` table of ``eid``, ``bmi``, ``sbp``, ``diagnoses`` and ``case``.
    """
    con = duckdb.connect()
    for alias, filename in (
        ("d", "string_fields2.csv"),
        ("m", "real_fields1.csv"),
        ("b", "integer_arrays_part1.csv"),
    ):
        con.execute(
            f"create view {alias} as select * from read_csv('{raw}/*/{filename}', "
            "header=true, all_varchar=true, sample_size=-1)"
        )
    codes = [name for name in con.execute("select * from d limit 0").df().columns if name.startswith("41270-")]
    stacked = ", ".join(f'"{name}"' for name in codes)
    con.execute(f"""
        create table people as
        select m.eid,
               try_cast(m."21001-0.0" as double) as bmi,
               try_cast(b."4080-0.0" as double) as sbp,
               coalesce(u.diagnoses, 0) as diagnoses,
               coalesce(u.is_case, false) as is_case
        from m
        join b using (eid)
        left join (
            select eid,
                   count(code) filter (where code <> '') as diagnoses,
                   max(code like 'E11%') as is_case
            from (select eid, unnest([{stacked}]) as code from d)
            group by eid
        ) u using (eid)
    """)
    return con


CHAPTERS = ("A-B", "C-D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S-T", "V-Y", "Z")
MIN_DIAGNOSES = 5


def chapter_of(code: str) -> str:
    """Name the ICD-10 chapter a code belongs to.

    Args:
        code: An ICD-10 code such as ``E119``.

    Returns:
        The chapter label, or ``other`` if the first letter is unknown.
    """
    letter = code[:1].upper()
    for chapter in CHAPTERS:
        if letter in chapter.split("-") or (len(chapter) == 3 and chapter[0] <= letter <= chapter[2]):
            return chapter
    return "other"


def profiles(raw: Path, eids: np.ndarray) -> np.ndarray:
    """Build each person's share of diagnoses per ICD-10 chapter.

    Args:
        raw: Directory holding one folder per centre.
        eids: Participants, in the order the rows should come out.

    Returns:
        A person by chapter matrix of shares, rows summing to one where anything was recorded.
    """
    con = duckdb.connect()
    con.execute(
        f"create view d as select * from read_csv('{raw}/*/string_fields2.csv', "
        "header=true, all_varchar=true, sample_size=-1)"
    )
    codes = [name for name in con.execute("select * from d limit 0").df().columns if name.startswith("41270-")]
    stacked = ", ".join(f'"{name}"' for name in codes)
    pairs = con.execute(f"select eid, code from (select eid, unnest([{stacked}]) as code from d) where code <> ''").df()
    position = {name: index for index, name in enumerate(CHAPTERS)}
    row_of = {eid: index for index, eid in enumerate(eids)}
    counts = np.zeros((len(eids), len(CHAPTERS)))
    for eid, code in zip(pairs["eid"].to_numpy(), pairs["code"].to_numpy(), strict=True):
        chapter = chapter_of(code)
        if chapter in position and eid in row_of:
            counts[row_of[eid], position[chapter]] += 1
    totals = counts.sum(axis=1, keepdims=True)
    return np.divide(counts, totals, out=np.zeros_like(counts), where=totals > 0)


def embedding(shares: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Embed the chapter profiles of the people with enough diagnoses.

    Args:
        shares: Person by chapter shares.
        keep: Which people to embed.

    Returns:
        Two-dimensional coordinates for the kept people.
    """
    edata = EHRData(shares[keep].astype(np.float64))
    edata.var_names = list(CHAPTERS)
    ehrdata.infer_feature_types(edata, output=None)
    ep.pp.scale_norm(edata)
    ep.pp.pca(edata, n_comps=10)
    ep.pp.neighbors(edata, n_neighbors=30)
    ep.tl.umap(edata)
    return edata.obsm["X_umap"]


def quality(people: pd.DataFrame) -> pd.DataFrame:
    """Summarise the participants with ehrapy's quality metrics.

    Args:
        people: One row per participant.

    Returns:
        One row per variable, as :func:`ehrapy.preprocessing.qc_metrics` returns it.
    """
    columns = ["bmi", "sbp", "diagnoses"]
    edata = EHRData(people[columns].to_numpy(dtype=np.float64))
    edata.var_names = columns
    edata.obs["is_case"] = pd.Categorical(np.where(people["is_case"].to_numpy(), "E11", "no E11"))
    ehrdata.infer_feature_types(edata, output=None)
    return ep.pp.qc_metrics(edata)[1]


def cohort_funnel(sites: Path) -> dict[str, int]:
    """Count how many people survive each cohort rule.

    Args:
        sites: Directory of mapped OMOP sites.

    Returns:
        One count per stage, in order.
    """
    spec = of.FeatureSpec(CANDIDATES, VOCABULARY_VERSION, LOOKBACK_DAYS, missing_indicators=True)
    people = eligible = followed = incident = 0
    for path in site_paths(sites):
        source = of.OmopSource(path)
        people += source.connection.execute("select count(*) from person").fetchone()[0]
        index = index_table(source)
        eligible += index.num_rows
        person_ids, _ = of.feature_matrix(source, spec, index)
        followed += len(person_ids)
        kept = {int(person) for person in person_ids}
        labels = np.asarray(index.column("label"))
        ids = np.asarray(index.column("person_id"))
        incident += int(sum(label for person, label in zip(ids, labels, strict=True) if int(person) in kept))
    return {
        "sampled": SAMPLED,
        "in these centres": people,
        "no diabetes yet": eligible,
        "followed past\nthe landmark": followed,
        "incident cases": incident,
    }


def funnel(axis: plt.Axes, stages: dict[str, int]) -> None:
    """Draw how the cohort narrows.

    Args:
        axis: Axes to draw on.
        stages: Counts per stage.
    """
    names = list(stages)
    values = [stages[name] for name in names]
    axis.barh(range(len(names)), values, color=[LOCAL] * (len(names) - 1) + [CASE])
    axis.set_yticks(range(len(names)))
    axis.set_yticklabels(names, fontsize=8)
    axis.invert_yaxis()
    axis.set_xscale("log")
    axis.set_xlabel("people (log scale)", fontsize=9)
    for position, value in enumerate(values):
        axis.text(value * 1.2, position, f"{value:,}", va="center", fontsize=7, color=MUTED)
    axis.set_xlim(right=max(values) * 6)
    axis.set_title("Who is left to train on", fontsize=10)


def implausible(axis: plt.Axes, sbp: np.ndarray, metrics: pd.DataFrame) -> None:
    """Show systolic blood pressure against the range the spec accepts.

    Args:
        axis: Axes to draw on.
        sbp: Systolic readings.
        metrics: The ehrapy quality table.
    """
    low, high = SBP_RANGE
    ceiling = metrics.loc["sbp", "max"]
    values = sbp[~np.isnan(sbp) & (sbp > 0)]
    axis.hist(values, bins=np.logspace(np.log10(values.min()), np.log10(values.max()), 60), color=LOCAL)
    axis.axvspan(low, high, color=ACCENT, alpha=0.15)
    for edge in (low, high):
        axis.axvline(edge, color=ACCENT, linewidth=1)
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel("systolic blood pressure (mmHg)", fontsize=9)
    axis.set_ylabel("readings (log scale)", fontsize=9)
    kept = float(((values >= low) & (values <= high)).mean())
    sentinel = int((values == ceiling).sum())
    axis.set_title(f"{sentinel} readings sit at {ceiling:,.0f} mmHg; the range keeps {kept:.0%}", fontsize=10)


def load(axis: plt.Axes, counts: np.ndarray, metrics: pd.DataFrame) -> None:
    """Show how many diagnoses people carry.

    Args:
        axis: Axes to draw on.
        counts: Dated diagnoses per person.
        metrics: The ehrapy quality table.
    """
    axis.hist(counts, bins=np.arange(0, metrics.loc["diagnoses", "max"] + 5, 5), color=LOCAL)
    axis.set_xlabel("diagnoses per person", fontsize=9)
    axis.set_ylabel("people", fontsize=9)
    none = float((counts == 0).mean())
    recorded = np.median(counts[counts > 0])
    axis.set_title(f"{none:.0%} have no hospital record, the rest a median of {recorded:.0f}", fontsize=10)


def separation(axis: plt.Axes, bmi: np.ndarray, case: np.ndarray) -> None:
    """Overlay the BMI distributions of the diabetic group and the rest.

    Args:
        axis: Axes to draw on.
        bmi: Body mass index per person.
        case: True where that person has an E11 code.
    """
    observed = ~np.isnan(bmi)
    bins = np.linspace(np.nanmin(bmi), np.nanmax(bmi), 50)
    axis.hist(
        bmi[observed & ~case], bins=bins, density=True, color=LOCAL, label=f"no E11 ({(observed & ~case).sum():,})"
    )
    axis.hist(
        bmi[observed & case],
        bins=bins,
        density=True,
        histtype="step",
        linewidth=1.6,
        color=CASE,
        label=f"E11 ({(observed & case).sum():,})",
    )
    axis.set_xlabel("body mass index (kg/m²)", fontsize=9)
    axis.set_ylabel("density", fontsize=9)
    axis.legend(fontsize=7, frameon=False)
    difference = float(np.nanmean(bmi[case]) - np.nanmean(bmi[~case]))
    axis.set_title(f"BMI differs by {difference:+.2f} kg/m² between the groups", fontsize=10)


def map_panel(axis: plt.Axes, coordinates: np.ndarray, values: np.ndarray, title: str, label: str) -> None:
    """Draw the embedding coloured by one quantity.

    Args:
        axis: Axes to draw on.
        coordinates: Two-dimensional coordinates.
        values: One value per point.
        title: Panel title.
        label: Colour bar label.
    """
    order = np.random.default_rng(0).permutation(len(coordinates))
    points = axis.scatter(
        coordinates[order, 0], coordinates[order, 1], s=1.2, alpha=0.4, c=values[order], cmap="viridis"
    )
    bar = plt.colorbar(points, ax=axis, shrink=0.8)
    bar.set_label(label, fontsize=8)
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_xlabel("UMAP1", fontsize=8)
    axis.set_ylabel("UMAP2", fontsize=8)
    axis.set_title(title, fontsize=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=HERE / "data" / "raw")
    parser.add_argument("--sites", type=Path, default=HERE / "data" / "omop")
    parser.add_argument("--out", type=Path, default=HERE / "cohort_overview_ukb.png")
    parser.add_argument("--title", default="UK Biobank synthetic extract, before any modelling")
    args = parser.parse_args()

    people = person_table(args.raw).execute("select * from people order by eid").df()
    bmi = people["bmi"].to_numpy()
    sbp = people["sbp"].to_numpy()
    counts = people["diagnoses"].to_numpy()
    case = people["is_case"].to_numpy().astype(bool)
    metrics = quality(people)
    print(metrics[["missing_values_pct", "median", "min", "max"]].round(2).to_string())
    stages = cohort_funnel(args.sites)
    print(" -> ".join(f"{name.replace(chr(10), ' ')} {value:,}" for name, value in stages.items()))

    shares = profiles(args.raw, people["eid"].to_numpy())
    keep = counts >= MIN_DIAGNOSES
    coordinates = embedding(shares, keep)
    print(f"embedded {keep.sum():,} people with at least {MIN_DIAGNOSES} diagnoses")

    figure, axes = plt.subplots(2, 3, figsize=(15.5, 8.4))
    funnel(axes[0, 0], stages)
    implausible(axes[0, 1], sbp, metrics)
    load(axes[0, 2], counts, metrics)
    separation(axes[1, 0], bmi, case)
    map_panel(axes[1, 1], coordinates, counts[keep], "Chapter profile, by history length", "diagnoses")
    map_panel(
        axes[1, 2],
        coordinates,
        shares[keep][:, list(CHAPTERS).index("E")] * 100,
        "The same map, by endocrine share",
        "% of codes in E",
    )
    for axis in axes.ravel():
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(args.title, fontsize=12)
    figure.tight_layout()
    figure.savefig(args.out, dpi=170)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
