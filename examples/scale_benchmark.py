from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import duckdb
import numpy as np
import torch
from torch import nn

import omopflare as of

HERE = Path(__file__).parent
MIMIC = HERE / "mimic_mortality" / "data"
SCRATCH = HERE / ".scale"
OUT = HERE / "scale_benchmark.json"

MULTIPLIERS = (10, 100, 1000)
ROWS_PER_PATIENT = 3386

SPEC = of.FeatureSpec(
    features=(
        of.Feature("resp_rate", 3024171, "measurement", unit_concept_id=8541, plausible_range=(4.0, 60.0)),
        of.Feature("heart_rate", 3027018, "measurement", unit_concept_id=8483, plausible_range=(20.0, 220.0)),
        of.Feature("spo2", 40762499, "measurement", unit_concept_id=8554, plausible_range=(50.0, 100.0)),
        of.Feature("diastolic", 21492240, "measurement", unit_concept_id=8876, plausible_range=(20.0, 150.0)),
        of.Feature("systolic", 21492239, "measurement", unit_concept_id=8876, plausible_range=(40.0, 250.0)),
        of.Feature("potassium", 3023103, "measurement", unit_concept_id=9557, plausible_range=(1.5, 9.0)),
    ),
    vocabulary_version="mimic-iv-demo-omop-0.9",
    lookback_days=3650,
    missing_indicators=True,
)


class Net(nn.Module):
    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features
        self.net = nn.Sequential(nn.Linear(n_features, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def peak_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def replicate(multiplier: int) -> Path:
    """Copy MIMIC's real measurement rows across more patients, keeping its distributions.

    Every value, unit, concept and interval comes from the real export, so cardinality stays realistic rather than
    compressing to almost nothing the way generated data does.

    Args:
        multiplier: How many copies of the 100-patient cohort to make.

    Returns:
        The directory holding the scaled CDM.
    """
    target = SCRATCH / f"x{multiplier}"
    if (target / "measurement.parquet").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"create view src_person as select * from read_csv('{MIMIC / 'person.csv'}', sample_size = -1)")
    con.execute(f"create view src_meas as select * from read_csv('{MIMIC / 'measurement.csv'}', sample_size = -1)")
    con.execute("create view dense as select person_id, row_number() over (order by person_id) as seq from src_person")
    con.execute(
        f"""copy (
            select d.seq * {multiplier} + c.copy as person_id, p.year_of_birth
            from src_person p join dense d on d.person_id = p.person_id cross join range({multiplier}) c(copy)
        ) to '{target / "person.parquet"}' (format parquet)"""
    )
    con.execute(
        f"""copy (
            select row_number() over () as measurement_id,
                   d.seq * {multiplier} + c.copy as person_id,
                   m.measurement_concept_id,
                   m.measurement_date,
                   m.measurement_type_concept_id,
                   m.value_as_number,
                   m.unit_concept_id,
                   m.measurement_source_value
            from src_meas m join dense d on d.person_id = m.person_id cross join range({multiplier}) c(copy)
        ) to '{target / "measurement.parquet"}' (format parquet)"""
    )
    return target


def stages(directory: Path) -> dict[str, float]:
    """Time each stage a federated client actually runs, from scan to training epoch.

    Args:
        directory: A CDM directory produced by :func:`replicate`.

    Returns:
        Seconds and rows per second for each stage, plus peak memory.
    """
    source = of.OmopSource(directory)
    people = source.count("person")
    rows = source.count("measurement")
    index = """
        select person_id, max(measurement_date) + interval '1' day as index_date
        from measurement group by person_id
    """

    start = time.perf_counter()
    seen = sum(batch.num_rows for batch in of.extract(source, SPEC, index, batch_size=200_000))
    scan = time.perf_counter() - start

    start = time.perf_counter()
    ids, matrix = of.design_matrix(source, SPEC, index)
    build = time.perf_counter() - start

    labels = (np.random.default_rng(0).random(len(ids)) < 0.15).astype(np.float64)
    scaler = of.SiteStats.from_matrix(matrix)
    dataset = of.CohortDataset(np.nan_to_num(of.standardize(matrix, scaler)), labels)
    loader = of.dataloader(dataset, batch_size=512, shuffle=True)

    model = Net(SPEC.width)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    model.train()
    start = time.perf_counter()
    for features, batch in loader:
        optimizer.zero_grad()
        criterion(model(features), batch).backward()
        optimizer.step()
    epoch = time.perf_counter() - start

    return {
        "people": people,
        "measurement_rows": rows,
        "scan_seconds": scan,
        "build_seconds": build,
        "epoch_seconds": epoch,
        "scan_people_per_second": seen / scan,
        "build_people_per_second": len(ids) / build,
        "epoch_people_per_second": len(ids) / epoch,
        "peak_gb": peak_gb(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Throughput of the federated client path on MIMIC-shaped data.")
    parser.add_argument("--multipliers", type=int, nargs="*", default=list(MULTIPLIERS))
    args = parser.parse_args()

    results = []
    for multiplier in args.multipliers:
        directory = replicate(multiplier)
        row = stages(directory)
        results.append(row)
        print(
            f"{row['people']:>9,} patients {row['measurement_rows']:>14,} rows | "
            f"scan {row['scan_seconds']:6.2f}s  build {row['build_seconds']:6.2f}s  "
            f"epoch {row['epoch_seconds']:6.2f}s | "
            f"peak {row['peak_gb']:5.1f} GB",
            flush=True,
        )
    OUT.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
