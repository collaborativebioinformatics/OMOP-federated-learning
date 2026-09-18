# omopflare

Federated learning on OMOP CDM data with NVFlare, for cohorts that do not fit in memory.

```bash
pip install -e .
```

## Quickstart

```python
import omopflare as of

spec = of.FeatureSpec(
    features=(
        of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),
        of.Feature("t2dm", 201826, "condition_occurrence"),
    ),
    vocabulary_version="v5.0 31-AUG-24",
    lookback_days=365,
)

site = of.OmopSource("/data/omop/site_a")
of.validate(site, spec, strict=True)

person_ids, X = of.design_matrix(site, spec, "select person_id, current_date as index_date from person")
```

The index is SQL, a duckdb relation or an Arrow table, and needs `person_id` and `index_date`.
Use `of.extract` instead of `of.design_matrix` to stream batches rather than build one matrix.

## End-to-end NVFlare job

Two files, both runnable as written against `synthea_cohorts/cohort_2/data/omop`.

`fl_model.py`, kept importable because NVFlare rebuilds the model on the server from a class path:

```python
import torch
from torch import nn


class Net(nn.Module):
    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features
        self.net = nn.Sequential(nn.Linear(n_features, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(torch.nan_to_num(x)).squeeze(-1)
```

`fl_client.py`, what each site runs:

```python
import argparse

import nvflare.client as flare
import torch
from fl_model import Net
from torch import nn

import omopflare as of

parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True)
parser.add_argument("--spec", required=True)
args = parser.parse_args()

spec = of.FeatureSpec.from_json(args.spec)
site = of.OmopSource(args.site)
index = """
    select p.person_id,
           cast(p.year_of_birth + 50 || '-01-01' as date) as index_date,
           cast(count(c.person_id) > 0 as double) as label
    from person p
    left join condition_occurrence c on c.person_id = p.person_id and c.condition_concept_id = 201826
    group by p.person_id, p.year_of_birth
"""

scaler = of.site_statistics(site, spec, index)
cohort = of.StreamingCohort(site, spec, index, scaler=scaler)
loader = of.dataloader(cohort, batch_size=64)

model = Net(spec.width)
criterion = nn.BCEWithLogitsLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)

flare.init()
while flare.is_running():
    received = flare.receive()
    if received is None:
        break
    model.load_state_dict(received.params)
    model.train()
    steps = 0
    for features, labels in loader:
        optimizer.zero_grad()
        criterion(model(features), labels).backward()
        optimizer.step()
        steps += 1
    flare.send(
        flare.FLModel(
            params={k: v.cpu() for k, v in model.state_dict().items()},
            meta={"NUM_STEPS_CURRENT_ROUND": steps},
        )
    )
```

`fl_run.py`, the server side, plain NVFlare:

```python
from pathlib import Path

from fl_model import Net
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

import omopflare as of

sites = sorted(Path("synthea_cohorts/cohort_2/data/omop").iterdir())
spec = of.FeatureSpec(
    features=(of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),),
    vocabulary_version="synthea-contract",
    lookback_days=3650,
)
spec.to_json("spec.json")

recipe = FedAvgRecipe(
    name="omopflare_demo",
    model=Net(spec.width),
    min_clients=len(sites),
    num_rounds=5,
    train_script="fl_client.py",
)
set_per_site_config(
    recipe,
    {s.name: {"train_args": f"--site {s.resolve()} --spec {Path('spec.json').resolve()}"} for s in sites},
)
recipe.execute(SimEnv(clients=[s.name for s in sites], workspace_root="workspace"))
```

NVFlare is not wrapped; anything in its docs works unchanged.

## What the API enforces

The spec is frozen at run time because FedAvg averages weight tensors, so every site must produce the same width and column order.
Derive it from a counting round rather than writing it blind: `concept_counts` reports suppressed per-site counts and `propose_spec` keeps what enough sites can supply.

Numeric features pin a `unit_concept_id`; rows in other units are dropped, not converted.

`validate` errors on a vocabulary mismatch, a non-standard or invalid concept, and an unmapped rate above `max_unmapped`.
In OMOP `concept_id = 0` means present but unmapped, not absent.

`extract` reads only events strictly before `index_date`, within `lookback_days`, and inside the observation period.
A patient whose landmark falls outside their coverage drops out rather than contributing a row of nulls.

`leakage_report` checks whether being measured, rather than what was measured, predicts the outcome.
It returns per-feature prevalence among measured and unmeasured patients, and errors when presence alone decides the label.

`SiteStats` holds count, sum and sum of squares, which combine into one scaler.
Min and max are excluded because each is a single patient's value.
`suppressed` and `prevalence` take a `min_cell_count`, default 5.

## Time series

`extract_sequence` bins the lookback into a patient by feature by time-bin `sparse.COO` with a NaN `fill_value`, so an unmeasured bin stays distinct from a measured zero.

```python
for person_ids, tensor in of.extract_sequence(site, spec, index, bins=10, aggregate="mean"):
    edata = of.to_ehrdata(person_ids, tensor, spec)
```

`to_ehrdata` keeps the tensor sparse.

## Training

`CohortDataset` holds a site in memory, `StreamingCohort` re-runs the query per epoch, and `dataloader` wraps either.
`dataloader` indexes a batch at a time rather than a row at a time, which on a 1M by 4 cohort is 2.88M rows/s against 0.61M for the torch default.
`CohortDataset` keeps a sparse matrix in CSR and densifies per batch, so a 200k by 2000 matrix at 2% occupancy holds 0.097 GB rather than the 1.60 GB its dense form needs.

Two sparse libraries, split by dimensionality: pydata/sparse for the 3D sequence tensors, which scipy cannot express, and scipy CSR for the 2D training matrices, whose batched row indexing plus densify runs at 4.6M rows/s against 0.27M for pydata GCXS compressed on axis 0.

## Scale

Tables stay on disk as duckdb views over Parquet or CSV, with concept and date filters pushed into the scan.
Building the design matrix for 1M patients over 50M measurement rows takes 0.8s, but that is the duckdb scan alone; a training epoch over the result is the slower half.
duckdb's buffer pool will use the memory it is given, so set `memory_limit` if that matters.
Parquet sorted by `person_id` is fastest.

Table and column names are matched case-insensitively, so OHDSI exports with `PERSON.csv` and `PERSON_ID` load unchanged.
