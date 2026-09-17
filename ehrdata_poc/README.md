# Federated learning on ICU time series with NVFlare

A small end-to-end demo: an ICU cohort is loaded as EHRData, explored with ehrapy, trained across four simulated sites with NVFlare FedAvg and PyTorch, then evaluated back in ehrapy.

## Pipeline

```bash
<<<<<<< HEAD
python prepare.py     # PhysioNet 2012 -> EHRData -> per-site shards + hourly.h5ed
python federate.py    # NVFlare FedAvg across the four ICUs
=======
python run.py             # build_dataset.py, then federate.py
```

Or step by step:

```bash
python build_dataset.py   # PhysioNet 2012 -> EHRData -> per-site shards + hourly.h5ed
python federate.py        # NVFlare FedAvg across the four ICUs
>>>>>>> 6ba7973bc62ef21f76e776be04885809a37155e3
```

Then work through the notebooks on the `Python (nvflare)` kernel, in order.

<<<<<<< HEAD
`01_explore.ipynb` explores the cohort with ehrapy and writes back the UMAP embedding, tensor intact.
=======
`01_explore.ipynb` explores the cohort with ehrapy and writes the object back to `hourly.h5ed` with the UMAP embedding, tensor intact.
>>>>>>> 6ba7973bc62ef21f76e776be04885809a37155e3
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

<<<<<<< HEAD
## Why not OMOP

This started on GiBleed (OHDSI Eunomia, CDM 5.3) loaded through `ehrdata.io.omop`, which worked but could not support the analysis.
Two things killed it.

Its namesake outcome is unlearnable: every drug's GI hemorrhage rate sits at the base prevalence of 0.178, including celecoxib and ibuprofen, and a centralized model reaches AUROC 0.46-0.51.
Peptic ulcer was learnable but only to about 0.61.

More importantly the drug exposure tensor is 98.5% NaN and contains exactly one distinct value, `1.0`, because `is_present` encodes absence as NaN rather than zero.
With 13.9 distinct drugs per patient out of 113, that sparsity is inherent rather than a defect, but it leaves no trajectory to model.
DTW needs dense numeric series, and GiBleed has zero non-null `value_as_number` across all 44053 measurement rows.
Synthea27NJ has real numeric measurements but only 28 patients, too few to federate.

PhysioNet 2012 is not OMOP, which is the cost of the switch.
It buys real trajectories, a real outcome, and sites that are actual ICUs rather than an artificial split.

## Notes

### Dynamic time warping

`01_explore.ipynb` builds the neighbor graph two ways: from the flattened hourly representation with a euclidean metric, and from trajectory alignment with `ep.pp.neighbors(edata, metric="dtw", use_rep=...)` on the 3D tensor.
`use_rep` must name a 3D array in `.layers` or `.obsm`; passing `"X"` raises.

Variables must be scaled first.
Raw units span three orders of magnitude, from a standard deviation of 0.00 for `MechVent` to 1649 for `AST`, so an unscaled distance tracks whichever high-variance variable happens to be observed.
Scoring the neighbor graph by how often neighbors share an outcome, against 0.786 expected by chance, gives 0.802 on raw units and 0.848 after scaling.

Coverage also limits what the metric can do: `timeseries_distance` only uses variables where both patients have more than three observed timepoints, and a median pair shares just 9 of 37.
Only `GCS`, `HR`, `Temp` and `Urine` clear that bar for more than 90% of patients.

DTW is quadratic in patients, so the notebook uses a stratified subsample of 500; the full cohort extrapolates to roughly a day.
Parallelizing it through `neighbors(transformer=KNeighborsTransformer(..., n_jobs=-1))` is slower, not faster, because joblib ships the tensor to every worker.

Only `ep.tl.rank_features_groups` and the euclidean `pca`/`neighbors` path are 2D-only.
`ep.pp.qc_metrics` and `neighbors(metric="dtw")` operate on the 3D tensor directly.

### NVFlare
=======
## NVFlare
>>>>>>> 6ba7973bc62ef21f76e776be04885809a37155e3

`federate.py` uses the 2.9 Recipe API (`FedAvgRecipe` + `SimEnv` + `set_per_site_config`) rather than the older `FedAvgJob` + `ScriptRunner` + `simulator_run`.
The recipe takes `model=` and `min_clients=` where the job takes `initial_model=` and `n_clients=`.

The model class must live in its own importable module.
NVFlare walks the client script's imports with `ast` and copies the sources into the job's `custom/` directory, and the server rebuilds the model from a class path.
Defining the model in the launcher script fails at server startup.

The server reconstructs the initial model through `FedJobConfig._get_args`, which reads `component.__dict__` for attributes named after `__init__` parameters.
`MortalityMLP` therefore assigns `self.n_features`; without it the rebuild raises `missing 1 required positional argument`.

`SimEnv` nests its output under `{workspace_root}/{recipe name}/server/simulate_job/app_server/`, which is where `best_FL_global_model.pt` lands.
The per-round server log is one level up, at `{workspace_root}/{recipe name}/server/log.txt`.

<<<<<<< HEAD
### ehrdata

The OMOP path hit a round-trip bug: `ed.io.write_h5ed` cannot write what `ed.io.omop.setup_obs` produces, because of datetime64 and all-null object columns.
Reported as [theislab/ehrdata#303](https://github.com/theislab/ehrdata/issues/303).
Nothing in this repo works around it any more, since `physionet2012` returns an EHRData that writes cleanly.

=======
>>>>>>> 6ba7973bc62ef21f76e776be04885809a37155e3
## Development

`ruff check .` and `ruff format .` are configured in `pyproject.toml` at line length 120, and cover the notebooks as well as the modules.

## Environment

Conda env `nvflare`: python 3.14, nvflare 2.9.0, torch 2.12.0, ehrdata 0.4.0, ehrapy 0.15.0.
Training runs on CPU; the tensors are small enough that a GPU is not worth the transfer.
