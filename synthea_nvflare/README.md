# Federated Cox PH demo on the Synthea cohort

This demo follows the structure of `ehrdata_poc`, but reads the normalized Synthea CSV files into
PyTorch datasets. It simulates several clients from the single available cohort, gives every client
its own stratified train/test split, and federates a linear Cox proportional-hazards model with
NVFlare FedAvg.

## Important outcome limitation

The current 98-patient cohort has 18 deaths over full follow-up, but **zero deaths within one year of
the diabetes diagnosis**. The default endpoint is therefore five-year survival, which has three
observed deaths. This is enough to exercise the software, but not enough for stable clinical inference.

For stable client splits, `build_dataset.py` supports two modes:

- `--outcome demo` (default): real Synthea predictors with explicitly simulated five-year event times,
  targeting about 40–45% events for stable client train/test splits. This is not clinical data. Adjust
  the prevalence with `--demo-logit-intercept` (higher values produce more events).
- `--outcome observed`: genuine Synthea mortality, truncated at five years (only three events).

The horizon is configurable with `--horizon-days`; an all-censored endpoint exits with a clear error.

Every generated shard records `outcome_source`, so demo labels cannot be mistaken for observed data.

## Prediction landmark and features

The prediction landmark is the first type-2-diabetes diagnosis. Only measurements on or before that
date are used; post-diagnosis observations are excluded to prevent leakage. For each biomarker, the
closest eligible measurement is selected.

Features are:

- age at diabetes diagnosis and number of diabetes diagnosis records;
- HbA1c, blood and serum/plasma glucose, BMI, systolic blood pressure, eGFR, and blood and
  serum/plasma creatinine;
- one missingness indicator for each biomarker.

Because every row currently has the same diagnosis concept (`OMOP 201826`), that constant is not used
as a predictor. Diagnosis timing and multiplicity carry the available diagnosis information.
Missing values are median-imputed and continuous features standardized using only each client's
training rows.

## Run

Use Python 3.10+ with NVFlare and PyTorch:

```bash
cd synthea_nvflare
python -m pip install -e .
python run.py --cohorts cohort_1,cohort_2
```

`--cohorts` accepts a comma-separated list of folders under `synthea_cohorts/`.
Each cohort is treated as one federated client and receives its own stratified
train/test split and client-local preprocessing. FedAvg weights updates by the number
of training examples processed, which matters when cohort sizes differ substantially.
For example:

```bash
python run.py --cohorts cohort_1,cohort_2,cohort_3
```

When only one cohort is supplied, it is partitioned into three simulated clients so
the run remains federated:

```bash
python run.py --cohorts cohort_1
```

Change that number with `--single-cohort-clients`, for example
`--single-cohort-clients 5`.

Use `--build-only` to prepare and inspect the PyTorch client shards without starting
NVFlare.

Or run the stages separately:

```bash
python build_dataset.py --n-clients 3 --outcome demo --horizon-days 1825
python federate.py --rounds 20 --epochs 5
python visualize.py
```

The visualization step writes:

- `results/prediction_dashboard.png`: federated validation metrics by round, held-out
  patient survival predictions, and global Cox coefficients;
- `results/predictions.csv`: patient-level client, outcome, predicted survival, and
  log-risk values for further analysis;
- `results/coefficients.csv`: global coefficients and hazard ratios.

After federation, `run.py` writes language-neutral files under `results/<run-name>/`:
`predictions.csv`, `coefficients.csv`, `round_metrics.csv`, and `run_manifest.json`.
It also updates `results/latest_run.json`.

For an RStudio report, open `visualize_predictions.Rmd` and click **Knit**. It reads
the latest result files directly and does not invoke Python. To render an older run,
change the R Markdown `result` parameter from `latest` to its folder name. Install the
core R dependencies once:

```r
install.packages(c("jsonlite", "ggplot2"))
```

Install `plotly` and `DT` for interactive charts and a searchable table; when they are
not installed, the report automatically uses static ggplot charts and a standard table.

Predicted survival uses the best global model with a Breslow baseline hazard estimated
from each client's local training set. Because features are standardized independently
at each client, compare survival probabilities rather than raw log-risk across clients.

To demonstrate why the one-year endpoint cannot be fitted:

```bash
python build_dataset.py --outcome observed --horizon-days 365
```

This currently reports that zero one-year deaths are available and stops.

## PyTorch and Cox details

Each `.pt` shard is loaded through `torch.utils.data.Dataset` and `DataLoader`; no `EHRData` object is
used. Cox partial likelihood needs a risk set, so local training deliberately uses one full-batch
DataLoader per client rather than invalid random mini-batches. The network is a single bias-free
linear layer, making it a conventional Cox PH linear predictor.

Clients report:

- Harrell's concordance index on their local test set;
- five-year Brier score from a Breslow baseline hazard estimated on local training data;
- partial log-likelihood loss.

FedAvg averages the coefficient tensors. This is a demonstration of NVFlare orchestration. Averaging
locally fitted Cox coefficients is not mathematically identical to maximizing a pooled partial
likelihood because risk sets do not cross sites.

Generated shards and NVFlare workspaces are ignored by Git.
