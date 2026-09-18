from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import nvflare.client as flare
import torch
from cohort import CDM, index_sql
from model import RiskNet
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of


def load(spec: of.FeatureSpec, site: int, seed: int):
    """Build this site's train and test cohorts.

    Args:
        spec: The agreed feature schema.
        site: Which shard of the cohort this client owns.
        seed: Seed for the train/test split.

    Returns:
        The train loader, the test loader and the positive class weight.
    """
    source = of.OmopSource(CDM)
    index = index_sql(site)
    ids, X = of.design_matrix(source, spec, index)
    table = source.sql(index).arrow().read_all()
    labels = dict(zip(table.column("person_id").to_pylist(), table.column("label").to_pylist(), strict=True))
    y = np.array([labels[int(person)] for person in ids], dtype=np.float64)

    held_out = (ids.astype(np.int64) & 3) == (seed % 4)
    scaler = of.SiteStats.from_matrix(X[~held_out])
    scaled = np.nan_to_num(of.standardize(X, scaler))
    positives = max(y[~held_out].sum(), 1.0)
    weight = float((len(y[~held_out]) - positives) / positives)
    return (
        of.dataloader(of.CohortDataset(scaled[~held_out], y[~held_out]), batch_size=32, shuffle=True),
        of.dataloader(of.CohortDataset(scaled[held_out], y[held_out]), batch_size=256),
        weight,
    )


def auroc(model: nn.Module, loader) -> float:
    """Score a model, returning NaN when the held-out labels are single-class.

    Args:
        model: Model to score.
        loader: Batches to score on.

    Returns:
        The AUROC, or NaN if only one class is present.
    """
    model.eval()
    scores, labels = [], []
    with torch.no_grad():
        for features, batch in loader:
            scores.append(torch.sigmoid(model(features)).numpy())
            labels.append(batch.numpy())
    scores, labels = np.concatenate(scores), np.concatenate(labels)
    if len(np.unique(labels)) < 2:
        return float("nan")
    return float(roc_auc_score(labels, scores))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=int, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--lr", type=float, default=1e-2)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    spec = of.FeatureSpec.from_json(args.spec)
    train_loader, test_loader, weight = load(spec, args.site, args.seed)
    model = RiskNet(spec.width)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight, dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        score = auroc(model, test_loader)
        model.train()
        steps = 0
        for _ in range(args.epochs):
            for features, labels in train_loader:
                optimizer.zero_grad()
                criterion(model(features), labels).backward()
                optimizer.step()
                steps += 1
        flare.send(
            flare.FLModel(
                params={key: value.cpu() for key, value in model.state_dict().items()},
                metrics={"auroc": 0.5 if np.isnan(score) else score},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


if __name__ == "__main__":
    main()
