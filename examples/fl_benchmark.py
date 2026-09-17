from __future__ import annotations

import copy
import json
import time
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
import torch
from sklearn.metrics import roc_auc_score
from torch import nn

import omopflare as of

HERE = Path(__file__).parent
OUT = HERE / "fl_benchmark.json"
CDM = HERE / ".synthea1k"
BUCKET = "https://synthea-omop.s3.amazonaws.com/synthea1k"
TABLES = ("person", "condition_occurrence", "drug_exposure", "observation_period")

OUTCOME = 4217975
LANDMARK = "2015-01-01"
ROUNDS = 20
SEEDS = 5


class Net(nn.Module):
    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features
        self.net = nn.Sequential(nn.Linear(n_features, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def fetch() -> Path:
    """Download the public Synthea OMOP CDM export, which needs no credentials."""
    CDM.mkdir(exist_ok=True)
    for table in TABLES:
        target = CDM / f"{table}.csv"
        if not target.exists():
            urllib.request.urlretrieve(f"{BUCKET}/{table}.csv", target)
    return CDM


def cohort(source: of.OmopSource) -> tuple[np.ndarray, np.ndarray, of.FeatureSpec]:
    """Build an incident-prediction cohort: who acquires the outcome after the landmark.

    Args:
        source: The CDM to read.

    Returns:
        The design matrix, the labels and the spec used.
    """
    common = f"""
        select condition_concept_id from condition_occurrence
        where condition_concept_id > 0 and condition_concept_id <> {OUTCOME}
          and condition_start_date < date '{LANDMARK}'
        group by 1 having count(distinct person_id) >= 30 order by count(distinct person_id) desc limit 30
    """
    drugs = f"""
        select drug_concept_id from drug_exposure
        where drug_concept_id > 0 and drug_exposure_start_date < date '{LANDMARK}'
        group by 1 having count(distinct person_id) >= 30 order by count(distinct person_id) desc limit 30
    """
    spec = of.FeatureSpec(
        features=tuple(
            [of.Feature(f"c{r[0]}", r[0], "condition_occurrence") for r in source.sql(common).fetchall()]
            + [of.Feature(f"d{r[0]}", r[0], "drug_exposure") for r in source.sql(drugs).fetchall()]
        ),
        vocabulary_version="synthea-omop-1k",
        lookback_days=3650,
    )
    index = f"""
        select p.person_id, date '{LANDMARK}' as index_date, 2015 - p.year_of_birth as age,
               cast(max(case when c.condition_concept_id = {OUTCOME}
                             and c.condition_start_date >= date '{LANDMARK}' then 1 else 0 end) as double) as label
        from person p
        left join condition_occurrence c on c.person_id = p.person_id
        where p.person_id not in (
            select person_id from condition_occurrence
            where condition_concept_id = {OUTCOME} and condition_start_date < date '{LANDMARK}'
        )
        group by p.person_id, p.year_of_birth
    """
    ids, X = of.design_matrix(source, spec, index)
    table = source.sql(index).arrow().read_all()
    meta = dict(
        zip(
            table.column("person_id").to_pylist(),
            zip(table.column("age").to_pylist(), table.column("label").to_pylist(), strict=True),
            strict=True,
        )
    )
    age = np.array([[meta[int(i)][0]] for i in ids], dtype=np.float64)
    y = np.array([meta[int(i)][1] for i in ids], dtype=np.float64)
    return np.hstack([np.nan_to_num(X), age]), y, spec


def fit(X: np.ndarray, y: np.ndarray, epochs: int, init: dict | None = None) -> Net:
    model = Net(X.shape[1])
    if init is not None:
        model.load_state_dict(init)
    positives = max(y.sum(), 1.0)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor((len(y) - positives) / positives, dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-3)
    loader = of.dataloader(of.CohortDataset(X, y), batch_size=32, shuffle=True)
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
        return float(roc_auc_score(y, torch.sigmoid(model(torch.from_numpy(X).float())).numpy()))


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


def fragmentation_curve() -> list[dict[str, float]]:
    """Hold the total cohort fixed and split it across more and more sites."""
    X, y, spec = cohort(of.OmopSource(fetch()))
    print(f"cohort {len(y)} patients, {int(y.sum())} events, {X.shape[1]} features ({spec.width} from the spec)")

    rows = []
    for sites in (1, 2, 4, 8, 16):
        local, federated, pooled = [], [], []
        for seed in range(SEEDS):
            rng = np.random.default_rng(seed)
            order = rng.permutation(len(y))
            cut = int(0.7 * len(y))
            train, test = order[:cut], order[cut:]
            Xtr, ytr, Xte, yte = X[train], y[train], X[test], y[test]
            scaler = of.SiteStats.from_matrix(Xtr)
            Xtr, Xte = of.standardize(Xtr, scaler), of.standardize(Xte, scaler)

            parts = np.array_split(rng.permutation(len(ytr)), sites)
            shards = [(Xtr[p], ytr[p]) for p in parts if ytr[p].sum() > 0]
            local.append(float(np.mean([auroc(fit(sx, sy, 40), Xte, yte) for sx, sy in shards])))
            federated.append(auroc(fedavg(shards), Xte, yte))
            pooled.append(auroc(fit(Xtr, ytr, 40), Xte, yte))
        row = {
            "sites": sites,
            "patients_per_site": int(0.7 * len(y) / sites),
            "local": float(np.mean(local)),
            "federated": float(np.mean(federated)),
            "centralized": float(np.mean(pooled)),
        }
        rows.append(row)
        print(
            f"{sites:2d} sites ({row['patients_per_site']:3d}/site)  "
            f"local {row['local']:.3f}  federated {row['federated']:.3f}  centralized {row['centralized']:.3f}",
            flush=True,
        )
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
    accuracy = fragmentation_curve()
    print()
    scale = throughput()
    OUT.write_text(json.dumps({"accuracy": accuracy, "throughput": scale}, indent=2) + "\n")
