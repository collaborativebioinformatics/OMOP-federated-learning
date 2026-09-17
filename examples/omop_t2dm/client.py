from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from model import RiskMLP
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of


def _split(n: int, test_fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    order = np.random.default_rng(seed).permutation(n)
    cut = round(n * (1 - test_fraction))
    return order[:cut], order[cut:]


def load(site: Path, spec: of.FeatureSpec, seed: int):
    """Build this site's train and test cohorts from its own OMOP tables.

    Args:
        site: Directory of OMOP tables for one site.
        spec: The frozen feature schema.
        seed: Seed for the train/test split.

    Returns:
        The train dataset, the test dataset and the site's statistics.
    """
    from cohort import index_table

    source = of.OmopSource(site)
    index = index_table(source)
    batches = list(of.extract(source, spec, index))
    person_ids = np.concatenate([of.to_matrix(b, spec)[0] for b in batches])
    features = np.vstack([of.to_matrix(b, spec)[1] for b in batches])

    labels = dict(zip(index.column("person_id").to_pylist(), index.column("label").to_pylist(), strict=True))
    y = np.array([labels[int(p)] for p in person_ids], dtype=np.float64)

    train, test = _split(len(y), 0.25, seed)
    stats = of.SiteStats.from_matrix(features[train])
    scaled = np.nan_to_num(of.standardize(features, stats))
    return (
        of.CohortDataset(scaled[train], y[train]),
        of.CohortDataset(scaled[test], y[test]),
        stats,
    )


def train_epochs(model: nn.Module, loader, epochs: int, lr: float) -> int:
    """Train in place and report the optimiser steps taken.

    Args:
        model: Model to update.
        loader: Training batches.
        epochs: Passes over the loader.
        lr: Learning rate.

    Returns:
        Number of optimiser steps.
    """
    positive = sum(float(labels.sum()) for _, labels in loader)
    total = sum(len(labels) for _, labels in loader)
    weight = torch.tensor((total - positive) / max(positive, 1.0), dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(pos_weight=weight)
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
    scores = of.predict(model, loader)
    labels = np.concatenate([labels.numpy() for _, labels in loader])
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
    train_set, test_set, _ = load(args.site, spec, args.seed)
    train_loader = of.dataloader(train_set, batch_size=64, shuffle=True)
    test_loader = of.dataloader(test_set, batch_size=256)
    model = RiskMLP(spec.width)

    of.run_client(
        model,
        train=lambda m: train_epochs(m, train_loader, args.epochs, args.lr),
        evaluate=lambda m: auroc(m, test_loader),
    )


if __name__ == "__main__":
    main()
