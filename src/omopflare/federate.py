from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from .stats import SiteStats, combine


def fedavg_recipe(
    name: str,
    model: nn.Module,
    train_script: str | Path,
    sites: Sequence[str],
    *,
    num_rounds: int = 20,
    key_metric: str = "auroc",
    train_args: Mapping[str, str] | None = None,
):
    """Build an NVFlare FedAvg recipe with one client per site.

    The model class must live in an importable module, because NVFlare rebuilds it on the server from a class path.

    Args:
        name: Job name.
        model: Initial global model.
        train_script: Client script NVFlare launches per site.
        sites: Client names, one per site.
        num_rounds: Aggregation rounds.
        key_metric: Metric the server tracks to keep the best global model.
        train_args: Per-site command line arguments, keyed by site name.

    Returns:
        A configured ``FedAvgRecipe``.
    """
    from nvflare.app_opt.pt.recipes import FedAvgRecipe
    from nvflare.recipe import set_per_site_config

    recipe = FedAvgRecipe(
        name=name,
        model=model,
        min_clients=len(sites),
        num_rounds=num_rounds,
        train_script=str(train_script),
        key_metric=key_metric,
    )
    if train_args:
        set_per_site_config(recipe, {site: {"train_args": train_args[site]} for site in sites})
    return recipe


def simulate(recipe, sites: Sequence[str], workspace: str | Path):
    """Run a recipe locally with one process per site.

    Args:
        recipe: A recipe from :func:`fedavg_recipe`.
        sites: Client names.
        workspace: Directory for the simulation workspace.

    Returns:
        The NVFlare run handle.
    """
    from nvflare.recipe import SimEnv

    return recipe.execute(SimEnv(clients=list(sites), workspace_root=str(Path(workspace).resolve())))


def global_model_path(workspace: str | Path, job: str) -> Path:
    """Locate the best global model a simulated run wrote.

    Args:
        workspace: The workspace root passed to :func:`simulate`.
        job: The recipe name.

    Returns:
        Path to ``best_FL_global_model.pt``.
    """
    return Path(workspace) / job / "server" / "simulate_job" / "app_server" / "best_FL_global_model.pt"


def run_client(
    model: nn.Module,
    train: Callable[[nn.Module], int],
    evaluate: Callable[[nn.Module], float],
    *,
    metric: str = "auroc",
) -> None:
    """Run the NVFlare receive, evaluate, train, send loop for one site.

    The incoming global model is scored before local training, so the server log records an out-of-sample curve.

    Args:
        model: Local model instance; weights are overwritten each round.
        train: Trains in place and returns the number of optimiser steps taken.
        evaluate: Scores a model on the site's held-out rows.
        metric: Name the server tracks.
    """
    import nvflare.client as flare

    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        score = evaluate(model)
        steps = train(model)
        flare.send(
            flare.FLModel(
                params={key: value.cpu() for key, value in model.state_dict().items()},
                metrics={metric: score},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


def federated_scaler(stats: Sequence[SiteStats]) -> SiteStats:
    """Combine per-site statistics into one scaler every site can standardise with.

    Args:
        stats: One summary per site, each already redacted to the site's disclosure threshold.

    Returns:
        The pooled statistics.
    """
    return combine(stats)


def predict(model: nn.Module, loader: DataLoader) -> np.ndarray:
    """Score every row a loader yields.

    Args:
        model: Trained model.
        loader: Loader over the rows to score.

    Returns:
        One probability per row, in loader order.
    """
    model.eval()
    scores = []
    with torch.no_grad():
        for features, _ in loader:
            scores.append(torch.sigmoid(model(features)).numpy())
    return np.concatenate(scores) if scores else np.empty(0)
