from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from cohort import CANDIDATES, COHORTS, LOOKBACK_DAYS, VOCABULARY_VERSION, index_table, site_paths
from model import RiskMLP
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

import omopflare as of

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"


def negotiate(paths: tuple[Path, ...], min_sites: int | None) -> of.FeatureSpec:
    """Agree a spec from what every site can actually supply.

    Args:
        paths: One directory per site.
        min_sites: Sites a feature must reach; defaults to all of them.

    Returns:
        The agreed spec.
    """
    counts = [of.concept_counts(of.OmopSource(path), CANDIDATES) for path in paths]
    for path, site in zip(paths, counts, strict=True):
        print(f"{path.name:8s} candidate patients {dict(site)}")
    spec = of.propose_spec(
        counts,
        CANDIDATES,
        vocabulary_version=VOCABULARY_VERSION,
        lookback_days=LOOKBACK_DAYS,
        min_sites=min_sites,
        missing_indicators=True,
    )
    print(f"agreed features: {list(spec.column_names)}\n")
    return spec


def report(paths: tuple[Path, ...], spec: of.FeatureSpec) -> list[of.SiteStats]:
    """Validate every site against the spec and print what each may disclose.

    Args:
        paths: One directory per site.
        spec: The agreed spec.

    Returns:
        One redacted summary per site.

    Raises:
        SystemExit: If any site fails validation.
    """
    summaries = []
    for path in paths:
        source = of.OmopSource(path)
        if failures := of.errors(of.validate(source, spec)):
            raise SystemExit("\n".join(str(f) for f in failures))
        index = index_table(source)
        stats = of.site_statistics(source, spec, index).redacted()
        rate = of.prevalence(np.asarray(index.column("label")))
        shown = "withheld" if rate is None else f"{rate:.1%}"
        print(f"{path.name:8s} {index.num_rows:6d} people, T2DM {shown:>8s}, observed {stats.n.tolist()}")
        summaries.append(stats)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Federated T2DM risk over any cohort's OMOP sites.")
    parser.add_argument("--sites", type=Path, default=COHORTS / "cohort_2" / "data" / "omop")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--job", default="omop_t2dm_fedavg")
    parser.add_argument("--min-sites", type=int, default=None)
    args = parser.parse_args()

    paths = site_paths(args.sites)
    spec = negotiate(paths, args.min_sites)
    spec.to_json(SPEC_PATH)
    scaler = of.combine(report(paths, spec))
    print(f"federated scaler mean {np.round(scaler.mean, 2).tolist()}\n")

    names = [p.name for p in paths]
    recipe = FedAvgRecipe(
        name=args.job,
        model=RiskMLP(spec.width),
        min_clients=len(names),
        num_rounds=args.rounds,
        train_script=str(HERE / "client.py"),
        key_metric="auroc",
    )
    set_per_site_config(
        recipe,
        {path.name: {"train_args": f"--site {path.resolve()} --spec {SPEC_PATH.resolve()}"} for path in paths},
    )
    workspace = HERE / "workspace" / args.sites.parent.parent.name
    run = recipe.execute(SimEnv(clients=names, workspace_root=str(workspace.resolve())))
    print(f"\nglobal model: {Path(run.get_result()) / 'server' / 'simulate_job' / 'app_server'}")


if __name__ == "__main__":
    main()
