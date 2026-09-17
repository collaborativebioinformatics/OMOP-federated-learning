# Federated T2DM risk across five OMOP sites

Runs `omopflare` end to end on [`synthea_cohorts/cohort_2`](../../synthea_cohorts/cohort_2), whose five sites are separate Synthea populations.

```bash
python run.py
```

That validates every site against the spec, prints what each may disclose, combines a federated scaler, then trains a `RiskMLP` with NVFlare FedAvg over ten rounds.

## What it does

`cohort.py` holds the spec and the cohort definition.
The landmark is each patient's fiftieth birthday and the label is a type 2 diabetes diagnosis after it, so the landmark does not depend on the outcome.
Features are the last BMI and systolic blood pressure in the ten years before the landmark, each pinned to a unit and a plausible range.

`client.py` is what NVFlare launches per site. It builds that site's cohort, standardises on its own training rows, and runs the receive, evaluate, train, send loop.
`run.py` drives the simulation.

## Results

Every site's validation AUROC climbs across rounds, which is the curve FedAvg should produce:

| site | round 1 | round 9 |
| --- | --- | --- |
| site_b | 0.44 | 0.51 |
| site_c | 0.50 | 0.56 |
| site_d | 0.32 | 0.71 |
| site_e | 0.15 | 0.74 |

The absolute numbers are poor, and that is the data rather than the plumbing.
Two features cannot predict diabetes, and Synthea does not simulate a strong relationship between them and the outcome.
Treat this as a demonstration that the pipeline runs, not as a result.

`site_a` is absent from the table because its prevalence is withheld: it is the 18 to 40 cohort, so almost nobody reaches the age-50 landmark and only 8 patients have a BMI in the window.
That is the minimum cell count doing its job.

## Sequence tensors

The same spec extracts a patient by feature by time-bin tensor:

```python
for person_ids, tensor = of.extract_sequence(source, SPEC, index, bins=10, aggregate="mean"):
    edata = of.to_ehrdata(person_ids, tensor, SPEC)
```

On `site_c` that is 1128 by 2 by 10 at about 5% density, which is why it is sparse rather than dense.
