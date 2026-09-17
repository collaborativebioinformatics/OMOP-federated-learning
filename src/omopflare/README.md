# omopflare

Federated learning on OMOP CDM data with NVFlare, for cohorts that do not fit in memory.

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
spec.to_json("spec.json")

site = of.OmopSource("/data/omop/site_a")
if failures := of.errors(of.validate(site, spec)):
    raise SystemExit("\n".join(str(f) for f in failures))

index = site.sql("select person_id, min(condition_start_date) as index_date from condition_occurrence group by 1")
for batch in of.extract(site, spec, index.arrow().read_all()):
    person_ids, X = of.to_matrix(batch, spec)
```

## Rules the API enforces

Write the spec once and ship it to every site; never build one per site.
Column position comes from `features`, so a site missing a concept gets an all-missing column instead of a narrower matrix.

Numeric features pin a `unit_concept_id`, and rows in other units are dropped rather than converted.
`plausible_range` is keyed on concept and unit together.

`validate` errors on a `vocabulary_version` mismatch, a non-standard or invalid concept, and an unmapped rate above `max_unmapped`.
In OMOP, `concept_id = 0` means present but unmapped, not absent.

`extract` reads only events strictly before `index_date`, within `lookback_days`, and inside the patient's observation period.
Set `missing_indicators=True` to add a `<name>_missing` column per numeric feature.

`SiteStats` holds count, sum and sum of squares, which combine across sites into one scaler.
Min and max are not included; each is a single patient's value.
`suppressed` and `prevalence` take a `min_cell_count`, default 5.

## Time series

`extract_sequence` splits the lookback into bins and returns a patient by feature by time-bin `sparse.COO`.
`fill_value` is NaN, so an unmeasured bin stays distinct from a measured zero, which is why this uses pydata/sparse rather than scipy.
Real occupancy is a few percent, so dense is not an option at scale.

```python
for person_ids, tensor in of.extract_sequence(site, spec, index, bins=10, aggregate="mean"):
    edata = of.to_ehrdata(person_ids, tensor, spec)
```

`to_ehrdata` hands the tensor to [ehrdata](https://github.com/theislab/ehrdata) rather than growing a second 3D container, so ehrapy works on it directly.

## Training

`CohortDataset` holds a site in memory; `StreamingCohort` re-runs the query per epoch for sites that do not fit.
`dataloader` wraps either, and ignores `shuffle` for the streaming one, which shuffles through a buffer instead.

`fedavg_recipe`, `simulate` and `run_client` wrap the NVFlare 2.9 recipe API.
The model class must be importable, because NVFlare rebuilds it on the server from a class path.

## Scale

Tables stay on disk as duckdb views over Parquet or CSV, with concept and date filters pushed into the scan.
`extract` streams Arrow batches and aggregates to one row per patient at the landmark.
`extract_sequence` joins against a registered feature table instead of one column per feature, so a spec of thousands of concepts stays one query.
Parquet sorted by `person_id` is fastest.

## Missing

Porting `examples/synthea_cox` onto the package, and a streaming variant of `extract_sequence` for cohorts whose tensors exceed memory one block at a time.
