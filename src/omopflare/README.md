# omopflare

Federated learning on OMOP CDM data with NVFlare, built for cohorts that do not fit in memory.

```python
import omopflare as of

spec = of.FeatureSpec(
    features=(
        of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),
        of.Feature("sbp", 3004249, "measurement", unit_concept_id=8876, plausible_range=(50.0, 250.0)),
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

## The spec is the contract

`FeatureSpec` is written once and shipped to every site.
Position in `features` is the column index, so a site never discovers its own features: one missing a concept contributes an all-missing column rather than a narrower matrix.
Without that, two sites produce different widths and FedAvg averages misaligned weights without raising anything.

Each numeric feature pins a `unit_concept_id`, and rows in any other unit are dropped rather than pooled.
Glucose in mg/dL and mmol/L differ about eighteen-fold, so mixing them is a correctness bug rather than noise.
`plausible_range` is keyed on the concept and unit together, matching how the OHDSI Data Quality Dashboard keys its bounds.

`vocabulary_version` is recorded because concept IDs are deprecated and demoted between vocabulary releases.
`validate` treats a mismatch as an error.

## Landmarking

`lookback_days` is required, and `extract` reads only events strictly before each patient's `index_date`.
A measurement recorded on the landmark itself is excluded, which is the leakage that is easiest to introduce and hardest to notice.
Rows are clipped to the patient's observation period, since absence outside that period is not evidence an event did not occur.

`missing_indicators=True` adds a `<name>_missing` column per numeric feature.
Whether a test was ordered is itself signal, so this is opt-in rather than automatic.

## Scale

Tables stay on disk. `OmopSource` registers them as duckdb views over Parquet or CSV, and `extract` pushes the concept and date filters down into the scan, so a MEASUREMENT table of billions of rows costs memory only for the rows that survive.
Results stream back as Arrow batches.

Parquet sorted by `person_id` is the fastest layout, because a patient's rows are then contiguous.
Nothing builds a dense patient by concept by time tensor; occupancy in real CDMs is a few percent, so the matrix is aggregated to one row per patient at the landmark.

## What may leave a site

`SiteStats` carries count, sum and sum of squares per column, which combine across sites into a global scaler.
None of the three is any individual's value. Minimum and maximum are deliberately absent, because each is a real patient's measurement.

`suppressed` and `prevalence` honour a minimum cell count, defaulting to 5.
Sites differ on the threshold, so set it to whatever the data-sharing agreement says.

Standardise with a scaler passed in, never one derived from another site's rows.

## Not done yet

The NVFlare client and recipe wiring, and a torch `Dataset` over the Arrow batches.
`examples/synthea_cox` still uses its own loader and has not been ported.
