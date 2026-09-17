# Federated learning on ICU time series with NVFlare

A small end-to-end demo: an ICU cohort is loaded as EHRData, explored with ehrapy, trained across four simulated sites with NVFlare FedAvg and PyTorch, then evaluated back in ehrapy.

## Pipeline

```bash
python run.py             # build_dataset.py, then federate.py
```

Or step by step:

```bash
python build_dataset.py   # PhysioNet 2012 -> EHRData -> per-site shards + hourly.h5ed
python federate.py        # NVFlare FedAvg across the four ICUs
```

Then work through the notebooks on the `Python (nvflare)` kernel, in order.

`01_explore.ipynb` explores the cohort with ehrapy and writes the object back to `hourly.h5ed` with the UMAP embedding, tensor intact.
`02_federated_results.ipynb` compares the federated model against local-only and centralized baselines and projects its risk scores onto that embedding.

`data.py`, `model.py` and `training.py` are shared by the scripts and the notebooks.
`client.py` is the per-site training script NVFlare launches; it is not run directly.

## Data

[PhysioNet 2012 Challenge](https://physionet.org/content/challenge-2012/) via `ehrdata.dt.physionet2012`, 11988 ICU stays with 37 vitals and labs over 48 hourly intervals.
The label is in-hospital mortality, prevalence 0.142.

The model sees the hourly tensor flattened to 1776 features, with the last observation carried forward and any remaining gaps filled by the population median for that variable and hour.
Features are standardized within each site using that site's training rows only, so no site sees another's statistics.

The EHRData object keeps all 48 intervals throughout; nothing is ever collapsed to a single timepoint.
Where ehrapy requires 2D input it is given a 2D view instead: `rank_features_groups` reads the `median` layer, and `neighbors` reads the flattened `X_flat` representation in `.obsm`.
A 2D layer is valid on a 3D EHRData, so the tensor is unaffected.

Sites are the ICU the patient was admitted to, which makes the federation non-IID in the way that actually matters:

| site | patients | mortality |
| --- | --- | --- |
| cardiac-surgery | 2528 | 0.049 |
| coronary-care | 1765 | 0.127 |
| surgical-icu | 3408 | 0.149 |
| medical-icu | 4287 | 0.199 |

## Results

Pooled held-out test set, AUROC:

| model | AUROC |
| --- | --- |
| local cardiac-surgery | 0.697 |
| local coronary-care | 0.719 |
| local medical-icu | 0.739 |
| local surgical-icu | 0.757 |
| centralized | 0.792 |
| **federated** | **0.803** |

More useful is per-site performance, each model scored on that site's own test set.
Federation beats every site's own local model on its own patients, which is the argument a participating hospital cares about:

| site | own local model | federated | centralized |
| --- | --- | --- | --- |
| cardiac-surgery | 0.761 | **0.806** | 0.757 |
| coronary-care | 0.775 | **0.787** | 0.784 |
| medical-icu | 0.742 | **0.798** | 0.791 |
| surgical-icu | 0.821 | **0.849** | 0.834 |

Training seeds are not pinned, so the numbers move slightly between runs while the ordering holds.

## NVFlare

`federate.py` uses the 2.9 Recipe API (`FedAvgRecipe` + `SimEnv` + `set_per_site_config`) rather than the older `FedAvgJob` + `ScriptRunner` + `simulator_run`.
The recipe takes `model=` and `min_clients=` where the job takes `initial_model=` and `n_clients=`.

The model class must live in its own importable module.
NVFlare walks the client script's imports with `ast` and copies the sources into the job's `custom/` directory, and the server rebuilds the model from a class path.
Defining the model in the launcher script fails at server startup.

The server reconstructs the initial model through `FedJobConfig._get_args`, which reads `component.__dict__` for attributes named after `__init__` parameters.
`MortalityMLP` therefore assigns `self.n_features`; without it the rebuild raises `missing 1 required positional argument`.

`SimEnv` nests its output under `{workspace_root}/{recipe name}/server/simulate_job/app_server/`, which is where `best_FL_global_model.pt` lands.
The per-round server log is one level up, at `{workspace_root}/{recipe name}/server/log.txt`.

## Development

`ruff check .` and `ruff format .` are configured in `pyproject.toml` at line length 120, and cover the notebooks as well as the modules.

## Environment

Conda env `nvflare`: python 3.14, nvflare 2.9.0, torch 2.12.0, ehrdata 0.4.0, ehrapy 0.15.0.
Training runs on CPU; the tensors are small enough that a GPU is not worth the transfer.
