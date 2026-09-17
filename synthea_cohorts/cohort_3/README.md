# Resampled demo cohort

50,000 patients resampled from [`cohort_1`](../cohort_1), in the same normalized table format.
It exists to give the federation code a cohort large enough to split into clients with stable train/test splits.

```bash
python generate_cohort.py
```

## Layout

```
data/cohort/patients.csv.gz      50,000 rows
data/cohort/diagnoses.csv.gz     50,000 rows
data/cohort/biomarkers.csv.gz    610,681 rows
```

The tables join on `patient_id` and match `cohort_1/data/cohort`, so anything reading that cohort reads this one.
`metadata.json` records the seed, the source template and the jitter applied.

## What this is not

This is not a fresh Synthea population.
It resamples 98 real Synthea patients with 3% biomarker jitter, so the effective sample size is still 98 regardless of the 50,000 rows.
Treat it as a load and plumbing fixture, not as data to draw conclusions from.

No raw Synthea export is retained, so the cohort cannot be traced back past `cohort_1`.
For a cohort with genuinely independent sites, use [`cohort_2`](../cohort_2), where each site is its own Synthea population.
