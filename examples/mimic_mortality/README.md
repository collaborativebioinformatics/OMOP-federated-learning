# Federated in-hospital mortality on MIMIC-IV

Real patient data, not Synthea.
The [MIMIC-IV demo in OMOP CDM](https://physionet.org/content/mimic-iv-demo-omop/0.9/) is open access on PhysioNet, so `run.py` downloads it without credentials.

```bash
python run.py
```

100 deidentified ICU patients, split into three sites by a hash of `person_id`.
The landmark is a day into each patient's first visit and the label is in-hospital death.
Features are the vitals that survive the counting round: respiratory rate, heart rate, SpO2, systolic and diastolic pressure, and potassium.

## What it shows

```
site_0:  28 patients,  6 deaths, prevalence     0.21
site_1:  39 patients,  4 deaths, prevalence withheld
site_2:  33 patients,  5 deaths, prevalence     0.15
```

site_1's prevalence is withheld by the minimum cell count.

This is a correctness demonstration, not a result: 100 patients and 15 deaths cannot support a mortality model.
Its value is that every check runs against a real OMOP export rather than one this repo generated, which is how the CSV type-inference and small-sample leakage bugs were found.
