# Raw UK Biobank to federated diabetes risk

UK Biobank's synthetic extract goes in, OMOP comes out, NVFlare trains an incident type 2 diabetes model across assessment centres without moving a row.

```bash
python ../../omop_skill/review/ukb/sample_ukb_fields.py --rows 100000 \
  --output ../../omop_skill/review/ukb/data/ukb_sampled   # raw UKB, ~2.7 GB
python prepare.py        # one raw folder per assessment centre
python to_omop.py        # raw -> OMOP, via the omop-etl skill
python explore.py        # what the source holds
python run.py            # omopflare + NVFlare FedAvg, against local and pooled
python figures.py
```

![cohort overview](cohort_overview_ukb.png)

## Steps

`prepare.py` splits people by assessment centre (field 54), which gives real sites where `split_sites.py` would split on `person_id % n`.
The wide UKB layout is left alone so the mapping does the reshaping.

`to_omop.py` runs the `omop-etl` skill with `omop_skill/mappings/ukb_pilot.yaml`.
All eight sites pass contract validation.

`explore.py` loads the participants as an `EHRData` and reports `ehrapy.preprocessing.qc_metrics`.
It reads the raw files, because the mapping keeps only the concepts the study needs.

`run.py` fits one model per site, one federated, one pooled.
All three see the same people, scaler, held-out set and number of passes.

## Mapping decisions

Every code starting `E11` maps to concept 201826.
The contract permits no more specific target.

Array slot 0 of field 41270 has no matching slot in 41280, so those 82,045 codes have no date and the inner join drops them.

The observation period spans every date UKB records, including diagnoses outside the study's scope.
Deriving it from `condition_occurrence` ends most people's period on their assessment day, and a later landmark then keeps only the people who already have diabetes.

## Cohort

The landmark is the day after the first assessment, so that assessment's measurements sit inside the lookback window.
Prevalent cases leave, and so do people whose record ends at the landmark, since an incident diagnosis needs follow-up.
That leaves 40,671 people across eight centres, 198 of them incident cases.

## What this data can and cannot show

UK Biobank draws every field independently, and the data agrees: BMI 27.42 against 27.52, sex 0.308 against 0.310, birth year 1952.7 against 1953.1.
No model beats chance, and none does.

That makes it a negative control.
Every AUROC interval covers 0.5 and the leakage report is clean.
Single sites still report apparent signal: the eight score 0.430 to 0.575 while federated scores 0.511, and their BMI coefficients run from -0.41 to +0.65 against a federated 0.05.

The overview says the same before any model runs, and says it about the sites too.

| | this cohort | [Synthea `cohort_2`](../synthea_diabetes) |
| --- | --- | --- |
| case rate across sites | 1.6x | 7.0x |
| worst site minus pooled mean | 0.03 SD | 0.79 SD |
| BMI medians span | 0.28 | 4.90 |
| BMI, cases minus controls | +0.06 SD | +0.38 SD |
| systolic BP kept by the plausible range | 94% | 100% |

Eight centres within 0.03 SD of the pooled mean are eight copies of the same cohort, so there is nothing for federation to reconcile.
Systolic blood pressure reaches 8,242 mmHg, and the plausible range drops those 6% as sentinels rather than measurements.

For the accuracy claim, [`../synthea_diabetes`](../synthea_diabetes) runs the same scripts on data with signal and with sites that differ.

## Limits

Age and sex live in `person`, and `omopflare` extracts concepts, so this model sees BMI and systolic blood pressure only.
Counts are small, so every AUROC carries a bootstrap interval.
