from __future__ import annotations

import csv
import gzip
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Dataset

DEFAULT_HORIZON_DAYS = 5 * 365
BIOMARKERS = (
    "hba1c",
    "glucose_blood",
    "glucose_serum_plasma",
    "bmi",
    "systolic_bp",
    "egfr",
    "creatinine_blood",
    "creatinine_serum_plasma",
)
CONTINUOUS_FEATURES = ("age_at_diagnosis", "diagnosis_count", *BIOMARKERS)
FEATURE_NAMES = (*CONTINUOUS_FEATURES, *(f"{name}_missing" for name in BIOMARKERS))


@dataclass
class CohortTensors:
    patient_ids: list[str]
    features: torch.Tensor
    durations: torch.Tensor
    events: torch.Tensor
    feature_names: tuple[str, ...]
    outcome_source: str


class SurvivalDataset(Dataset):
    def __init__(self, features: torch.Tensor, durations: torch.Tensor, events: torch.Tensor) -> None:
        self.features = features.float()
        self.durations = durations.float()
        self.events = events.float()

    def __len__(self) -> int:
        return self.features.shape[0]

    def __getitem__(self, index: int):
        return self.features[index], self.durations[index], self.events[index]


def _read(path: Path) -> list[dict[str, str]]:
    if path.exists():
        handle = path.open(mode="rt", newline="", encoding="utf-8-sig")
    else:
        compressed = path.with_suffix(path.suffix + ".gz")
        if not compressed.exists():
            raise FileNotFoundError(f"Missing {path} or {compressed}")
        handle = gzip.open(compressed, mode="rt", newline="", encoding="utf-8-sig")
    with handle:
        return list(csv.DictReader(handle))


def load_cohort(
    cohort_dir: Path,
    outcome: str,
    seed: int = 20260917,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    demo_logit_intercept: float = -0.25,
) -> CohortTensors:
    patients = _read(cohort_dir / "patients.csv")
    diagnoses = _read(cohort_dir / "diagnoses.csv")
    biomarkers = _read(cohort_dir / "biomarkers.csv")

    diagnosis_counts: dict[str, int] = defaultdict(int)
    for row in diagnoses:
        diagnosis_counts[row["patient_id"]] += 1

    # Closest measurement at or before diagnosis. Ties are resolved by measurement ID.
    latest: dict[tuple[str, str], tuple[int, str, float]] = {}
    for row in biomarkers:
        offset = int(row["days_from_index"])
        if offset > 0 or row["biomarker_name"] not in BIOMARKERS:
            continue
        key = (row["patient_id"], row["biomarker_name"])
        candidate = (offset, row["measurement_id"], float(row["value_as_number"]))
        if key not in latest or candidate[:2] > latest[key][:2]:
            latest[key] = candidate

    patient_ids: list[str] = []
    matrix: list[list[float]] = []
    observed_durations: list[float] = []
    observed_events: list[float] = []
    for row in patients:
        patient_id = row["patient_id"]
        values = [float(row["age_at_index"]), float(diagnosis_counts[patient_id])]
        missing = []
        for name in BIOMARKERS:
            measurement = latest.get((patient_id, name))
            values.append(float("nan") if measurement is None else measurement[2])
            missing.append(float(measurement is None))
        patient_ids.append(patient_id)
        matrix.append([*values, *missing])
        followup = int(row["survival_time_days"])
        event = int(row["death_event"]) == 1 and followup <= horizon_days
        observed_durations.append(float(min(followup, horizon_days)))
        observed_events.append(float(event))

    features = torch.tensor(matrix, dtype=torch.float32)
    durations = torch.tensor(observed_durations, dtype=torch.float32)
    events = torch.tensor(observed_events, dtype=torch.float32)
    if outcome == "observed":
        if events.sum() == 0:
            raise ValueError(
                f"Observed {horizon_days}-day endpoint has zero deaths; Cox partial likelihood is undefined. "
                "Use --outcome demo for an infrastructure demo or generate a larger/higher-risk cohort."
            )
        source = "observed_synthea_mortality"
    elif outcome == "demo":
        durations, events = simulate_demo_outcomes(features, seed, horizon_days, demo_logit_intercept)
        source = f"simulated_{horizon_days}_day_demo_outcome"
    else:
        raise ValueError(f"Unknown outcome mode: {outcome}")

    return CohortTensors(patient_ids, features, durations, events, FEATURE_NAMES, source)


def simulate_demo_outcomes(
    features: torch.Tensor, seed: int, horizon_days: int, logit_intercept: float = -0.25
) -> tuple[torch.Tensor, torch.Tensor]:
    """Generate learnable event times from real predictors, solely for the FL demo."""
    x = features[:, : len(CONTINUOUS_FEATURES)].clone()
    for column in range(x.shape[1]):
        observed = x[:, column][~torch.isnan(x[:, column])]
        fill = observed.median() if observed.numel() else torch.tensor(0.0)
        x[:, column] = torch.nan_to_num(x[:, column], nan=float(fill))
        std = x[:, column].std(unbiased=False)
        x[:, column] = (x[:, column] - x[:, column].mean()) / (std if std > 0 else 1.0)

    # age, diagnosis count, HbA1c, blood glucose, serum glucose, BMI, SBP, eGFR,
    # blood creatinine, serum creatinine. Coefficients are illustrative, not clinical.
    beta = torch.tensor([0.9, 0.0, 0.6, 0.25, 0.15, 0.3, 0.35, -0.65, 0.4, 0.25])
    logit = logit_intercept + x @ beta
    generator = torch.Generator().manual_seed(seed)
    event = torch.bernoulli(torch.sigmoid(logit), generator=generator)
    event_time = torch.randint(30, horizon_days + 1, (len(x),), generator=generator).float()
    duration = torch.where(event.bool(), event_time, torch.full_like(event_time, horizon_days))
    if event.sum() < 6:
        raise RuntimeError("Demo outcome unexpectedly produced too few events; choose a different seed.")
    return duration, event


def partition_clients(events: torch.Tensor, n_clients: int, seed: int) -> list[torch.Tensor]:
    if n_clients < 2:
        raise ValueError("At least two simulated clients are required for federation.")
    generator = torch.Generator().manual_seed(seed)
    groups = [[] for _ in range(n_clients)]
    for label in (1, 0):
        indices = torch.nonzero(events == label, as_tuple=True)[0]
        indices = indices[torch.randperm(len(indices), generator=generator)]
        for position, index in enumerate(indices.tolist()):
            groups[position % n_clients].append(index)
    return [torch.tensor(sorted(group), dtype=torch.long) for group in groups]


def stratified_split(events: torch.Tensor, test_fraction: float, seed: int) -> tuple[torch.Tensor, torch.Tensor]:
    generator = torch.Generator().manual_seed(seed)
    train: list[int] = []
    test: list[int] = []
    for label in (1, 0):
        indices = torch.nonzero(events == label, as_tuple=True)[0]
        indices = indices[torch.randperm(len(indices), generator=generator)]
        n_test = max(1, round(len(indices) * test_fraction)) if len(indices) > 1 else 0
        test.extend(indices[:n_test].tolist())
        train.extend(indices[n_test:].tolist())
    if not train or not test or events[train].sum() == 0:
        raise ValueError("A client split lacks training events; reduce --n-clients or increase the cohort.")
    return torch.tensor(sorted(train)), torch.tensor(sorted(test))


def preprocess(train: torch.Tensor, test: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    train, test = train.clone(), test.clone()
    n_continuous = len(CONTINUOUS_FEATURES)
    for column in range(n_continuous):
        observed = train[:, column][~torch.isnan(train[:, column])]
        median = observed.median() if observed.numel() else torch.tensor(0.0)
        train[:, column] = torch.nan_to_num(train[:, column], nan=float(median))
        test[:, column] = torch.nan_to_num(test[:, column], nan=float(median))
        mean = train[:, column].mean()
        std = train[:, column].std(unbiased=False)
        scale = std if std > 0 else torch.tensor(1.0)
        train[:, column] = (train[:, column] - mean) / scale
        test[:, column] = (test[:, column] - mean) / scale
    return train, test


def save_shards(
    cohort: CohortTensors, output: Path, n_clients: int, test_fraction: float, seed: int, horizon_days: int
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for client_number, rows in enumerate(partition_clients(cohort.events, n_clients, seed), start=1):
        local_events = cohort.events[rows]
        train_local, test_local = stratified_split(local_events, test_fraction, seed + client_number)
        train_rows, test_rows = rows[train_local], rows[test_local]
        x_train, x_test = preprocess(cohort.features[train_rows], cohort.features[test_rows])
        payload = {
            "X_train": x_train,
            "duration_train": cohort.durations[train_rows],
            "event_train": cohort.events[train_rows],
            "X_test": x_test,
            "duration_test": cohort.durations[test_rows],
            "event_test": cohort.events[test_rows],
            "patient_ids_train": [cohort.patient_ids[i] for i in train_rows.tolist()],
            "patient_ids_test": [cohort.patient_ids[i] for i in test_rows.tolist()],
            "feature_names": list(cohort.feature_names),
            "outcome_source": cohort.outcome_source,
            "horizon_days": horizon_days,
        }
        torch.save(payload, output / f"client-{client_number}.pt")


def save_site_shard(
    cohort: CohortTensors,
    output: Path,
    site_name: str,
    test_fraction: float,
    seed: int,
    horizon_days: int,
) -> Path:
    """Create one independently preprocessed train/test shard for one cohort/site."""
    train_rows, test_rows = stratified_split(cohort.events, test_fraction, seed)
    x_train, x_test = preprocess(cohort.features[train_rows], cohort.features[test_rows])
    payload = {
        "X_train": x_train,
        "duration_train": cohort.durations[train_rows],
        "event_train": cohort.events[train_rows],
        "X_test": x_test,
        "duration_test": cohort.durations[test_rows],
        "event_test": cohort.events[test_rows],
        "patient_ids_train": [cohort.patient_ids[i] for i in train_rows.tolist()],
        "patient_ids_test": [cohort.patient_ids[i] for i in test_rows.tolist()],
        "feature_names": list(cohort.feature_names),
        "outcome_source": cohort.outcome_source,
        "horizon_days": horizon_days,
        "cohort_name": site_name,
    }
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"client-{site_name}.pt"
    torch.save(payload, path)
    return path


def torch_load_compat(path: Path) -> dict:
    """Load trusted local shards on both modern and pre-2.0 PyTorch versions."""
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError as error:
        if "weights_only" not in str(error):
            raise
        return torch.load(path, map_location="cpu")


def load_shard(path: Path) -> dict:
    return torch_load_compat(path)


def make_loader(shard: dict, split: str) -> DataLoader:
    dataset = SurvivalDataset(shard[f"X_{split}"], shard[f"duration_{split}"], shard[f"event_{split}"])
    return DataLoader(dataset, batch_size=len(dataset), shuffle=False)
