"""Federated incident-diabetes risk across OMOP sites, against a local-only and a pooled reference.

Three models are fitted on the same training people, standardised with the same federated scaler,
and scored on the same held-out people, so the only difference between them is how much data each
one was allowed to see.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from cohort import CANDIDATES, LOOKBACK_DAYS, VOCABULARY_VERSION, site_paths
from model import RiskModel
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config
from training import auroc, auroc_interval, cohorts, fit, matrices, stack

import omopflare as of

HERE = Path(__file__).parent


def negotiate(paths: tuple[Path, ...]) -> of.FeatureSpec:
    """Agree a spec from what every site can actually supply.

    Args:
        paths: One directory per site.

    Returns:
        The agreed spec.
    """
    counts = [of.concept_counts(of.OmopSource(path), CANDIDATES) for path in paths]
    spec = of.propose_spec(
        counts,
        CANDIDATES,
        vocabulary_version=VOCABULARY_VERSION,
        lookback_days=LOOKBACK_DAYS,
        missing_indicators=True,
    )
    print(f"agreed features: {list(spec.column_names)}")
    return spec


def prepare(paths: tuple[Path, ...], spec: of.FeatureSpec) -> tuple[dict[str, dict], of.SiteStats]:
    """Validate every site, then build its cohorts and the federated scaler.

    Only counts, sums and sums of squares of the training half leave a site, so the scaler is
    federated and the held-out half never touches it.

    Args:
        paths: One directory per site.
        spec: The agreed spec.

    Returns:
        Per-site cohorts and the combined scaler.

    Raises:
        SystemExit: If any site fails validation.
    """
    sites, summaries = {}, []
    for path in paths:
        source = of.OmopSource(path)
        if failures := of.errors(of.validate(source, spec)):
            raise SystemExit("\n".join(str(failure) for failure in failures))
        train_index, test_index, source = cohorts(path, spec)
        summaries.append(of.site_statistics(source, spec, train_index))
        sites[path.name] = {"path": path, "source": source, "train": train_index, "test": test_index}
    return sites, of.combine(summaries)


def federated(paths: tuple[Path, ...], spec: of.FeatureSpec, args: argparse.Namespace) -> RiskModel:
    """Run FedAvg over the sites with NVFlare and return the global model.

    Args:
        paths: One directory per site.
        spec: The agreed spec.
        args: Parsed command-line arguments.

    Returns:
        The global model after the last round.
    """
    names = [path.name for path in paths]
    recipe = FedAvgRecipe(
        name=args.job,
        model=RiskModel(spec.width),
        min_clients=len(names),
        num_rounds=args.rounds,
        train_script=str(HERE / "client.py"),
        train_args=f"--epochs {args.epochs} --lr {args.lr}",
        key_metric="auroc",
    )
    set_per_site_config(
        recipe,
        {
            path.name: {
                "train_args": f"--site {path.resolve()} --spec {args.spec.resolve()} "
                f"--scaler {args.scaler.resolve()} --epochs {args.epochs} --lr {args.lr}"
            }
            for path in paths
        },
    )
    workspace = HERE / "workspace"
    run = recipe.execute(SimEnv(clients=names, workspace_root=str(workspace.resolve())))
    served = Path(run.get_result()) / "server" / "simulate_job" / "app_server" / "FL_global_model.pt"
    model = RiskModel(spec.width)
    model.load_state_dict(torch.load(served, weights_only=False)["model"])
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", type=Path, default=HERE / "data" / "omop")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--epochs", type=int, default=4, help="local epochs per round")
    parser.add_argument("--lr", type=float, default=5e-2)
    parser.add_argument("--job", default="ukb_diabetes_fedavg")
    parser.add_argument("--tag", default="ukb", help="suffix that keeps one run's artefacts apart from another's")
    args = parser.parse_args()
    args.spec = HERE / f"spec_{args.tag}.json"
    args.scaler = HERE / "data" / f"scaler_{args.tag}.npz"
    args.out = HERE / f"results_{args.tag}.json"

    paths = site_paths(args.sites)
    spec = negotiate(paths)
    spec.to_json(args.spec)
    sites, scaler = prepare(paths, spec)
    args.scaler.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.scaler, n=scaler.n, total=scaler.total, total_squared=scaler.total_squared)

    for name, site in sites.items():
        for half in ("train", "test"):
            site[half] = matrices(site["source"], spec, site[half], scaler)
        labels = site["train"][1]
        print(f"{name:>14s} {len(labels):>6,} train {len(site['test'][1]):>6,} test  incident {labels.mean():.2%}")

    # FedAvg sees rounds * epochs passes over each site, so every reference is given the same budget.
    passes = args.rounds * args.epochs
    pooled_train, pooled_test = stack([s["train"] for s in sites.values()]), stack([s["test"] for s in sites.values()])
    models = {name: fit(*site["train"], epochs=passes, lr=args.lr) for name, site in sites.items()}
    models["pooled"] = fit(*pooled_train, epochs=passes, lr=args.lr)
    models["federated"] = federated(paths, spec, args)

    results = {
        "features": list(spec.column_names),
        "sites": {name: {"train": len(s["train"][1]), "test": len(s["test"][1])} for name, s in sites.items()},
        "incidence": {name: float(s["train"][1].mean()) for name, s in sites.items()},
        "pooled_test": {name: auroc(model, *pooled_test) for name, model in models.items()},
        "pooled_test_ci": {name: auroc_interval(model, *pooled_test) for name, model in models.items()},
        "own_test": {name: auroc(models[name], *site["test"]) for name, site in sites.items()},
        "coefficients": {name: model.linear.weight.detach().numpy().ravel().tolist() for name, model in models.items()},
    }
    args.out.write_text(json.dumps(results, indent=2) + "\n")

    print("\nAUROC on the pooled held-out cohort")
    for name, score in results["pooled_test"].items():
        low, high = results["pooled_test_ci"][name]
        print(f"  {name:>14s} {score:.3f}  95% CI {low:.3f} to {high:.3f}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
