# Federated T2DM risk over OMOP sites

Runs `omopflare` end to end on any cohort under [`synthea_cohorts`](../../synthea_cohorts).

```bash
python run.py --sites ../../synthea_cohorts/cohort_2/data/omop
python run.py --sites ../../synthea_cohorts/cohort_3/data/omop --rounds 20
```

Each run counts what the sites can supply, agrees a spec, validates against it, combines a federated scaler, then trains a `RiskMLP` with NVFlare FedAvg.

`cohort.py` holds the candidate features and the cohort definition: the landmark is each patient's fiftieth birthday and the label is a later type 2 diabetes diagnosis.
`client.py` is what NVFlare launches per site.

## The spec is negotiated, not hand-written

Every site reports how many patients it has per candidate, suppressed below the minimum cell count, and `propose_spec` keeps what enough sites can supply:

| cohort | sites | agreed features |
| --- | --- | --- |
| cohort_1 | 3 | bmi, sbp, glucose, hba1c |
| cohort_2 | 5 | bmi, sbp |
| cohort_3 | 5 | bmi, sbp, glucose, hba1c |

cohort_2 has no glucose or HbA1c, so those drop out instead of becoming dead all-missing columns.
Pass `--min-sites` to keep a feature that only some sites carry.

## Results

On cohort_3, AUROC per site is about 0.74 and does not move across rounds.
That is convergence rather than a broken loop: locally the model goes from 0.48 to 0.74 within the first epoch and then plateaus.

The four biomarkers are written in bundles by Synthea, so a patient has all of them or none, and their variance is identical.
The model therefore has roughly two degrees of freedom, and most of the signal is whether a patient was measured at all.
Read this as working plumbing rather than a clinical result.

On cohort_2 the numbers are worse still, because only BMI and systolic pressure survive negotiation and neither predicts diabetes.
`site_a` there has its prevalence withheld by the minimum cell count: it is the 18 to 40 cohort, so almost nobody reaches the age-50 landmark.

## Sequence tensors

```python
for person_ids, tensor in of.extract_sequence(source, spec, index, bins=10, aggregate="mean"):
    edata = of.to_ehrdata(person_ids, tensor, spec)
```

On `cohort_2/site_c` that is 1128 by 2 by 10 at about 5% density, and `to_ehrdata` keeps it sparse.
