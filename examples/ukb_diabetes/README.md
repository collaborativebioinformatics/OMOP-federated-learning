# Raw UK Biobank to federated diabetes risk

The whole pipeline in one folder.
The official UK Biobank synthetic tabular extract goes in, OMOP comes out, and NVFlare trains an incident type 2 diabetes model across assessment centres without moving a row of patient data.

```bash
python ../../ukb_omop_agent/ukb/sample_ukb_fields.py --rows 100000 \
  --output ../../ukb_omop_agent/ukb/data/ukb_sampled   # raw UKB, ~2.7 GB
python prepare.py        # wide UKB TSVs -> one raw CSV folder per assessment centre
python to_omop.py        # raw CSVs -> OMOP, mapped and validated by the omop-etl skill
python explore.py        # what the mapped cohort looks like, with ehrapy
python run.py            # omopflare + NVFlare FedAvg, against local and pooled references
python figures.py
```

## What each step does

`prepare.py` is source preparation only.
It reshapes UKB's wide instance and array columns into per-event rows and splits people by assessment centre (field 54), which gives real sites rather than a random split.
No code becomes a concept here.

`to_omop.py` runs the `omop-etl` skill with `ukb_synthetic_v1.yaml` and validates every site against `contract/data_contract.md`.
All eight sites pass.

`explore.py` stacks the mapped sites into one `AnnData` and uses ehrapy for coverage, per-site distributions and a PCA.

`run.py` negotiates a feature spec across sites with `omopflare`, standardises with a scaler built from counts and sums that each site releases, then fits three models on the same people and scores them on the same held-out people: one per site alone, one federated with NVFlare FedAvg, and one pooled as the upper bound.
Every model gets the same number of passes over its data, so the only difference is how much data it may see.

## Mapping decisions

ICD-10 `E11.0` through `E11.9` all map to concept 201826, type 2 diabetes mellitus.
The contract permits only that concept, so the subtypes roll up to their parent instead of to the more specific SNOMED concepts a full Athena vocabulary would offer.

Array slot 0 of field 41270 has no matching slot in field 41280, so those 82,045 codes have no date and cannot become dated events.

The observation period spans a person's dated events, because the synthetic extract ships no enrolment table.

## Cohort

The landmark is the day after the first assessment, so that assessment's own measurements sit inside the lookback window and nothing is read on or after the landmark.
People already diagnosed before the landmark are prevalent cases and leave the cohort.
People whose record ends at the landmark leave too, because an incident diagnosis cannot be observed in someone with no follow-up.
That leaves 40,671 people across eight centres, 198 of them incident cases.

## What UK Biobank synthetic can and cannot show

UK Biobank generates each field of the synthetic dataset independently, and the data confirms it.
Mean BMI is 27.42 in the diabetic group and 27.52 in the rest, sex 0.308 against 0.310, birth year 1952.7 against 1953.1.
There is no association to find, so no model can beat chance here, and none does.

That makes this run a negative control, and a useful one.
Every AUROC interval covers 0.5, and `omopflare`'s leakage report finds nothing: per-feature AUROCs are 0.500 to 0.503.
Single sites still produce apparent signal, which is the point.
The eight per-centre models score between 0.444 and 0.560 on the same held-out cohort while the federated model scores 0.505, and single-site BMI coefficients range from below zero to above 0.6 while the federated estimate sits beside the pooled one.

The PCA shows the sites overlapping almost exactly, so the synthetic generator erases the geographic differences that make real assessment centres worth federating over.

## The same code where there is signal

Synthea models disease progression, so BMI genuinely predicts diabetes there, and `cohort_2` was built with per-site population parameters that make the sites differ.

```bash
python explore.py --sites ../../synthea_cohorts/cohort_2/data/omop \
  --out cohort_overview_synthea.png --title "Synthea cohort_2 after OMOP mapping"
python run.py --sites ../../synthea_cohorts/cohort_2/data/omop --job synthea_fedavg --tag synthea
python figures.py --results results_synthea.json --out federated_vs_local_synthea.png \
  --title "Incident type 2 diabetes across Synthea sites"
```

| model | AUROC | 95% CI |
| --- | --- | --- |
| site_d alone, 350 people | 0.433 | 0.343 to 0.523 |
| site_b alone | 0.541 | 0.447 to 0.635 |
| site_e alone | 0.563 | 0.465 to 0.659 |
| site_a alone | 0.578 | 0.493 to 0.666 |
| site_c alone | 0.606 | 0.518 to 0.694 |
| federated | 0.582 | 0.499 to 0.675 |
| pooled | 0.586 | 0.503 to 0.674 |

FedAvg recovers the pooled model, 0.582 against 0.586, without any site sharing a row.
The smallest site would have done worse than chance on its own and is the one federation helps most.
With 23 positives in the held-out cohort these intervals overlap heavily, so the claim this supports is that federated matches pooled and beats the weakest site, not that any single pair differs significantly.

## Limits

Demographics are not features: `omopflare` extracts concepts, and age and sex live in `person`, so this model sees BMI and systolic blood pressure only.
Counts are small, which is why every AUROC carries a bootstrap interval.
