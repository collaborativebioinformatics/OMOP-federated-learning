"""Loading, training and scoring shared by the NVFlare client and the local and pooled baselines."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import pyarrow as pa
import torch
from cohort import index_table, split
from model import RiskModel
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of

BATCH_SIZE = 256


def cohorts(site: Path, spec: of.FeatureSpec) -> tuple[pa.Table, pa.Table, of.OmopSource]:
    """Split one site's cohort into the training and the held-out half.

    Args:
        site: Directory of OMOP tables for one site.
        spec: The frozen feature schema.

    Returns:
        The training index, the test index and the site source.
    """
    source = of.OmopSource(site)
    index = index_table(source)
    held_out = pa.array(split(np.asarray(index.column("person_id"))))
    return index.filter(pa.compute.invert(held_out)), index.filter(held_out), source


def matrices(
    source: of.OmopSource, spec: of.FeatureSpec, index: pa.Table, scaler: of.SiteStats
) -> tuple[np.ndarray, np.ndarray]:
    """Materialise one cohort as a standardised feature matrix and its labels.

    Args:
        source: The site to read from.
        spec: The frozen feature schema.
        index: The cohort to extract.
        scaler: Statistics the features are standardised with.

    Returns:
        The feature matrix and the labels, aligned row by row.
    """
    person_ids, features = of.feature_matrix(source, spec, index)
    order = {int(person): position for position, person in enumerate(np.asarray(index.column("person_id")))}
    labels = np.asarray(index.column("label"), dtype=np.float64)[[order[int(p)] for p in person_ids]]
    return of.standardize(features, scaler), labels


def positive_weight(labels: np.ndarray) -> float:
    """Weight the positive class by its inverse frequency.

    Args:
        labels: Binary labels.

    Returns:
        The weight for :class:`~torch.nn.BCEWithLogitsLoss`.
    """
    positives = float(labels.sum())
    return (len(labels) - positives) / max(positives, 1.0)


def train(model: nn.Module, features: np.ndarray, labels: np.ndarray, *, epochs: int, lr: float) -> int:
    """Train a model in place on an in-memory cohort.

    Args:
        model: Model to update.
        features: Standardised feature matrix.
        labels: Binary labels.
        epochs: Passes over the data.
        lr: Learning rate.

    Returns:
        The number of optimiser steps taken.
    """
    x = torch.tensor(features, dtype=torch.float32)
    y = torch.tensor(labels, dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(positive_weight(labels), dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    generator = torch.Generator().manual_seed(0)
    model.train()
    steps = 0
    for _ in range(epochs):
        for batch in torch.randperm(len(y), generator=generator).split(BATCH_SIZE):
            optimizer.zero_grad()
            criterion(model(x[batch]), y[batch]).backward()
            optimizer.step()
            steps += 1
    return steps


def auroc(model: nn.Module, features: np.ndarray, labels: np.ndarray) -> float:
    """Score a model, returning NaN when the labels are single-class.

    Args:
        model: Model to score.
        features: Standardised feature matrix.
        labels: Binary labels.

    Returns:
        The AUROC, or NaN if only one class is present.
    """
    if len(np.unique(labels)) < 2:
        return float("nan")
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(torch.tensor(features, dtype=torch.float32))).numpy()
    return float(roc_auc_score(labels, scores))


def auroc_interval(
    model: nn.Module, features: np.ndarray, labels: np.ndarray, *, draws: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """Bootstrap a percentile interval for the AUROC.

    Args:
        model: Model to score.
        features: Standardised feature matrix.
        labels: Binary labels.
        draws: Bootstrap resamples.
        seed: Seed for the resampling.

    Returns:
        The 2.5th and 97.5th percentile of the resampled AUROC.
    """
    if len(np.unique(labels)) < 2:
        return float("nan"), float("nan")
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(torch.tensor(features, dtype=torch.float32))).numpy()
    rng = np.random.default_rng(seed)
    resampled = []
    for _ in range(draws):
        pick = rng.integers(0, len(labels), len(labels))
        if len(np.unique(labels[pick])) == 2:
            resampled.append(roc_auc_score(labels[pick], scores[pick]))
    return float(np.percentile(resampled, 2.5)), float(np.percentile(resampled, 97.5))


def fit(features: np.ndarray, labels: np.ndarray, *, epochs: int, lr: float) -> RiskModel:
    """Fit a fresh model on one cohort.

    Args:
        features: Standardised feature matrix.
        labels: Binary labels.
        epochs: Passes over the data.
        lr: Learning rate.

    Returns:
        The fitted model.
    """
    torch.manual_seed(0)
    model = RiskModel(features.shape[1])
    train(model, features, labels, epochs=epochs, lr=lr)
    return model


def stack(parts: Sequence[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-site matrices into one pooled cohort.

    Args:
        parts: Feature matrix and label pairs.

    Returns:
        The pooled feature matrix and labels.
    """
    return np.vstack([features for features, _ in parts]), np.concatenate([labels for _, labels in parts])


def coefficients(models: Iterable[nn.Module]) -> np.ndarray:
    """Read the linear coefficients of several models into one array.

    Args:
        models: Models with a ``linear`` layer.

    Returns:
        An array of shape ``(n_models, n_features)``.
    """
    return np.vstack([model.linear.weight.detach().numpy().ravel() for model in models])
