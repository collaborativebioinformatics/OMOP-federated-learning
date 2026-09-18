from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from cohort import CANDIDATES, CDM, LOOKBACK_DAYS, SITES, VOCABULARY_VERSION, fetch, index_sql
from model import RiskNet
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

import omopflare as of

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"


def survey() -> of.FeatureSpec:
    """Count what each site can supply, agree a spec, then report cohorts and leakage.

    Returns:
        The agreed spec.
    """
    counts = [of.concept_counts(of.OmopSource(CDM), CANDIDATES) for _ in range(SITES)]
    spec = of.propose_spec(
        counts,
        CANDIDATES,
        vocabulary_version=VOCABULARY_VERSION,
        lookback_days=LOOKBACK_DAYS,
        missing_indicators=True,
    )
    print(f"agreed features: {[f.name for f in spec.features]}\n")

    for site in range(SITES):
        source = of.OmopSource(CDM)
        index = index_sql(site)
        table = source.sql(index).arrow().read_all()
        labels = np.asarray(table.column("label"))
        rate = of.prevalence(labels)
        leaks, findings = of.leakage_report(source, spec, index)
        worst = max(leaks, key=lambda leak: abs(leak.auroc - 0.5))
        shown = "withheld" if rate is None else f"{rate:.2f}"
        print(
            f"site_{site}: {table.num_rows:3d} patients, {int(labels.sum()):2d} deaths, prevalence {shown:>8s}, "
            f"worst presence AUROC {worst.auroc:.2f} ({worst.name})"
        )
        for finding in findings:
            if finding.level == "error":
                raise SystemExit(str(finding))
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Federated in-hospital mortality across MIMIC-IV demo sites.")
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--job", default="mimic_mortality")
    args = parser.parse_args()

    fetch()
    spec = survey()
    spec.to_json(SPEC_PATH)

    names = [f"site_{site}" for site in range(SITES)]
    recipe = FedAvgRecipe(
        name=args.job,
        model=RiskNet(spec.width),
        min_clients=SITES,
        num_rounds=args.rounds,
        train_script=str(HERE / "client.py"),
        key_metric="auroc",
    )
    set_per_site_config(
        recipe,
        {name: {"train_args": f"--site {site} --spec {SPEC_PATH.resolve()}"} for site, name in enumerate(names)},
    )
    run = recipe.execute(SimEnv(clients=names, workspace_root=str((HERE / "workspace").resolve())))
    print(f"\nglobal model: {Path(run.get_result()) / 'server' / 'simulate_job' / 'app_server'}")


if __name__ == "__main__":
    main()
