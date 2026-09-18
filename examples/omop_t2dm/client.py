from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pyarrow as pa
import torch
from model import RiskMLP
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of


def load(site: Path, spec: of.FeatureSpec, seed: int):
    """Build this site's train and test cohorts without materialising them.

    Args:
        site: Directory of OMOP tables for one site.
        spec: The frozen feature schema.
        seed: Seed for the train/test split.

    Returns:
        The train cohort, the test cohort and the positive class weight.
    """
    from cohort import index_table

    source = of.OmopSource(site)
    index = index_table(source)
    fold = pa.compute.bit_wise_and(pa.compute.cast(index.column("person_id"), pa.int64()), pa.scalar(3, pa.int64()))
    held_out = pa.compute.equal(fold, pa.scalar(seed % 4, pa.int64()))
    train_index = index.filter(pa.compute.invert(held_out))
    test_index = index.filter(held_out)

    scaler = of.site_statistics(source, spec, train_index)
    labels = np.asarray(train_index.column("label"))
    positives = float(labels.sum())
    weight = (len(labels) - positives) / max(positives, 1.0)

    cohort = partial(of.StreamingCohort, source, spec, scaler=scaler, shuffle_buffer=4096)
    return cohort(train_index), cohort(test_index), weight


def train_epochs(model: nn.Module, loader, epochs: int, lr: float, weight: float) -> int:
    """Train in place and report the optimiser steps taken.

    Args:
        model: Model to update.
        loader: Training batches.
        epochs: Passes over the loader.
        lr: Learning rate.
        weight: Positive class weight.

    Returns:
        Number of optimiser steps.
    """
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight, dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    steps = 0
    for _ in range(epochs):
        for features, labels in loader:
            optimizer.zero_grad()
            criterion(model(features), labels).backward()
            optimizer.step()
            steps += 1
    return steps


def auroc(model: nn.Module, loader) -> float:
    """Score a model on a loader, returning NaN when the labels are single-class.

    Args:
        model: Model to score.
        loader: Batches to score on.

    Returns:
        The AUROC, or NaN if only one class is present.
    """
    model.eval()
    scores, labels = [], []
    with torch.no_grad():
        for features, batch_labels in loader:
            scores.append(torch.sigmoid(model(features)).numpy())
            labels.append(batch_labels.numpy())
    scores, labels = np.concatenate(scores), np.concatenate(labels)
    if len(np.unique(labels)) < 2:
        return float("nan")
    return float(roc_auc_score(labels, scores))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-2)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    spec = of.FeatureSpec.from_json(args.spec)
    train_set, test_set, weight = load(args.site, spec, args.seed)
    train_loader = of.dataloader(train_set, batch_size=64, shuffle=True)
    test_loader = of.dataloader(test_set, batch_size=256)
    model = RiskMLP(spec.width)

    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        score = auroc(model, test_loader)
        steps = train_epochs(model, train_loader, args.epochs, args.lr, weight)
        flare.send(
            flare.FLModel(
                params={key: value.cpu() for key, value in model.state_dict().items()},
                metrics={"auroc": score},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


if __name__ == "__main__":
    main()
