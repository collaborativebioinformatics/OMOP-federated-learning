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

site = of.OmopSource("/data/omop/site_a")
if failures := of.errors(of.validate(site, spec)):
    raise SystemExit("\n".join(str(f) for f in failures))

index = site.sql("select person_id, min(condition_start_date) as index_date from condition_occurrence group by 1")
for batch in of.extract(site, spec, index.arrow().read_all()):
    person_ids, X = of.to_matrix(batch, spec)
```

## Rules the API enforces

The spec is frozen at run time because FedAvg averages weight tensors, so every site must produce the same width and column order.
Derive it from a counting round rather than writing it blind: `concept_counts` reports suppressed per-site patient counts and `propose_spec` keeps what enough sites can supply.
A site missing a kept concept gets an all-missing column rather than a narrower matrix.

Numeric features pin a `unit_concept_id`; rows in other units are dropped, not converted.

`validate` errors on a `vocabulary_version` mismatch, a non-standard or invalid concept, and an unmapped rate above `max_unmapped`.
`concept_id = 0` means present but unmapped, not absent.

`extract` reads only events strictly before `index_date`, within `lookback_days`, and inside the observation period.

`SiteStats` holds count, sum and sum of squares, which combine into one scaler.
Min and max are excluded; each is a single patient's value.
`suppressed` and `prevalence` take a `min_cell_count`, default 5.

## Time series

`extract_sequence` bins the lookback into a patient by feature by time-bin `sparse.COO` with a NaN `fill_value`, so an unmeasured bin stays distinct from a measured zero.

```python
for person_ids, tensor in of.extract_sequence(site, spec, index, bins=10, aggregate="mean"):
    edata = of.to_ehrdata(person_ids, tensor, spec)
```

## Training

`CohortDataset` holds a site in memory, `StreamingCohort` re-runs the query per epoch, and `dataloader` wraps either.
NVFlare is used directly rather than wrapped; see `examples/omop_t2dm` for a `FedAvgRecipe` and a client loop.

## Scale

Tables stay on disk as duckdb views over Parquet or CSV, with concept and date filters pushed into the scan.
`extract_sequence` joins a registered feature table rather than emitting one column per feature, so a spec of thousands of concepts stays one query.
Parquet sorted by `person_id` is fastest.

## Missing

Porting `examples/synthea_cox` onto the package, and a streaming `extract_sequence` for tensors that exceed memory per block.
