from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import torch
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of

HERE = Path(__file__).parent
OUT = HERE / "fl_benchmark.json"
SITES = 5
FEATURES = 20
ROUNDS = 25


class Net(nn.Module):
    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features
        self.net = nn.Sequential(nn.Linear(n_features, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def make_cohort(n: int, rng: np.random.Generator, beta: np.ndarray, shift: float) -> tuple[np.ndarray, np.ndarray]:
    """Draw a site whose covariate means are shifted, so the sites are not identically distributed.

    Args:
        n: Patients to draw.
        rng: Source of randomness.
        beta: Shared coefficients, so one model can fit every site.
        shift: Per-site mean offset.

    Returns:
        The covariates and the binary outcome.
    """
    X = rng.normal(shift, 1.0, size=(n, len(beta)))
    p = 1.0 / (1.0 + np.exp(-(X @ beta)))
    return X, (rng.random(n) < p).astype(np.float64)


def fit(X: np.ndarray, y: np.ndarray, epochs: int, init: dict | None = None) -> Net:
    model = Net(X.shape[1])
    if init is not None:
        model.load_state_dict(init)
    loader = of.dataloader(of.CohortDataset(X, y), batch_size=64, shuffle=True)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-3)
    model.train()
    for _ in range(epochs):
        for features, labels in loader:
            optimizer.zero_grad()
            criterion(model(features), labels).backward()
            optimizer.step()
    return model


def auroc(model: Net, X: np.ndarray, y: np.ndarray) -> float:
    model.eval()
    with torch.no_grad():
        scores = torch.sigmoid(model(torch.from_numpy(X).float())).numpy()
    return float(roc_auc_score(y, scores))


def fedavg(shards: list[tuple[np.ndarray, np.ndarray]], rounds: int = ROUNDS) -> Net:
    """Average site weights each round, weighted by how many patients each site holds."""
    weights = np.array([len(y) for _, y in shards], dtype=float)
    weights /= weights.sum()
    state = Net(shards[0][0].shape[1]).state_dict()
    for _ in range(rounds):
        states = [fit(X, y, 1, copy.deepcopy(state)).state_dict() for X, y in shards]
        state = {k: sum(w * s[k].float() for w, s in zip(weights, states, strict=True)) for k in state}
    model = Net(shards[0][0].shape[1])
    model.load_state_dict(state)
    return model


def accuracy_curve() -> list[dict[str, float]]:
    rng = np.random.default_rng(0)
    beta = rng.normal(0, 0.6, size=FEATURES)
    shifts = np.linspace(-0.6, 0.6, SITES)
    Xte, yte = make_cohort(20000, np.random.default_rng(7), beta, 0.0)

    rows = []
    for n in (50, 100, 250, 500, 1000, 2500):
        shards = [make_cohort(n, rng, beta, s) for s in shifts]
        local = float(np.mean([auroc(fit(X, y, 30), Xte, yte) for X, y in shards]))
        federated = auroc(fedavg(shards), Xte, yte)
        pooled = auroc(fit(np.vstack([s[0] for s in shards]), np.concatenate([s[1] for s in shards]), 30), Xte, yte)
        rows.append({"per_site_n": n, "local": local, "federated": federated, "centralized": pooled})
        print(f"n={n:5d}/site  local {local:.3f}  federated {federated:.3f}  centralized {pooled:.3f}", flush=True)
    return rows


def throughput() -> list[dict[str, float]]:
    """Time a real extraction against synthetic OMOP parquet of increasing size."""
    spec = of.FeatureSpec(
        features=tuple(
            of.Feature(f"f{i}", 3000000 + i, "measurement", unit_concept_id=8840, plausible_range=(0.0, 1000.0))
            for i in range(4)
        ),
        vocabulary_version="bench",
        lookback_days=3650,
    )
    rows = []
    for people in (10_000, 100_000, 1_000_000):
        events = people * 50
        directory = HERE / f".bench_{people}"
        directory.mkdir(exist_ok=True)
        con = duckdb.connect()
        con.execute(
            f"copy (select i as person_id, 1970 as year_of_birth from range({people}) t(i)) "
            f"to '{directory / 'person.parquet'}' (format parquet)"
        )
        con.execute(
            f"""copy (select i as measurement_id, (i % {people}) as person_id,
                3000000 + (i % 4) as measurement_concept_id,
                date '2018-01-01' + cast((i * 7919) % 2000 as integer) as measurement_date,
                32817 as measurement_type_concept_id, 1.0 + (i % 100) as value_as_number,
                8840 as unit_concept_id, 's' as measurement_source_value
                from range({events}) t(i)) to '{directory / "measurement.parquet"}' (format parquet)"""
        )
        source = of.OmopSource(directory)
        index = "select person_id, date '2026-01-01' as index_date from person"
        start = time.perf_counter()
        seen = sum(len(of.to_matrix(b, spec)[0]) for b in of.extract(source, spec, index, batch_size=200_000))
        elapsed = time.perf_counter() - start
        rows.append({"people": people, "events": events, "seconds": elapsed, "people_per_second": seen / elapsed})
        print(f"{people:,} people / {events:,} rows -> {elapsed:.2f}s ({seen / elapsed:,.0f} people/s)", flush=True)
    return rows


if __name__ == "__main__":
    print("accuracy")
    accuracy = accuracy_curve()
    print("\nthroughput")
    scale = throughput()
    OUT.write_text(json.dumps({"accuracy": accuracy, "throughput": scale}, indent=2) + "\n")
