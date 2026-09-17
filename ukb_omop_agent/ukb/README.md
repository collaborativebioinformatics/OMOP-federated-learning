# UK Biobank synthetic data pilot

This folder prepares a small source dataset from the [official UK Biobank synthetic tabular release](https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html). It selects fields for sex, birth year, assessment date, body mass index, automated systolic blood pressure, ICD-10 diagnoses, and their corresponding dates. **This repository includes scripts only, not UKB data.** Downloaded files and generated extracts remain under ignored `data/`.

| UKB field | Meaning | Use in the pilot |
| --- | --- | --- |
| 31 | Sex | Participant information |
| 34 | Year of birth | Participant information and age |
| 53 | Assessment date | Date for assessment measurements |
| 21001 | Body mass index | Measurement |
| 4080 | Automated systolic blood pressure | Measurement |
| 41270 | ICD-10 diagnoses | Type 2 diabetes candidate codes (E11) |
| 41280 | First diagnosis dates | Dates paired with field 41270 by array index |

Run the commands below **from this `UKB` folder**. The download, subset and
mapping scripts use only the Python 3 standard library. The optional QC figure
requires Matplotlib.

The previous local run found 185 E11-positive participants in a 10,000-row sample; a new download may differ. The scripts below create the source extract needed by the adjacent OMOP agent. The [UKB synthetic dataset page](https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html) documents the source and cautions that its randomly generated values may be internally inconsistent.

For the E11-focused pilot, keep the existing **10,000-row** sample and select
every E11-positive participant in it. The current sample contains 185:

```bash
# Only if the 10,000-row sample is not already present:
python3 sample_ukb_fields.py --rows 10000 --output data/ukb_sampled

python3 make_ukb_subset.py \
  --input data/ukb_sampled \
  --output data/ukb_e11_pilot \
  --all-e11
```

The subset command selects every EID with an E11-family code and adds no
participants without one. The sampler checks that EIDs align across source
files. The published whole-file MD5 checksums do not apply to partial extracts.

To download and verify the **complete** field-group files instead:

```bash
# Preview which official field-group files contain the selected fields.
python3 download_ukb_fields.py

# Download those files and verify their published MD5 checksums.
python3 download_ukb_fields.py --download

# Select all E11-positive participants in the complete files.
python3 make_ukb_subset.py \
  --input data/ukb_tabular \
  --output data/ukb_e11_pilot \
  --all-e11
```

The result is `data/ukb_e11_pilot/ukb_subset.tsv` and a `manifest.json`. The TSV
has one row per selected EID and retains all available instances and arrays of
the seven fields. It is a UKB source extract, not yet OMOP data.

To test a basic four-table OMOP mapping against the downloaded Athena vocabulary:

```bash
python3 map_ukb_to_omop.py \
  --input data/ukb_e11_pilot/ukb_subset.tsv \
  --vocabulary /path/to/athena_vocabulary \
  --output data/ukb_omop_e11_pilot/all \
  --e11-only
```

This writes `person.csv`, `observation_period.csv`, `measurement.csv`,
`condition_occurrence.csv`, and `mapping_report.json`. It verifies the target
concept IDs against `CONCEPT.csv`. The E11 family is deliberately rolled up to
the broad standard type 2 diabetes concept 201826, so complication detail is
lost. The `--e11-only` option exports only E11-family diagnosis records.
Type concept IDs are 0 because their provenance has not been mapped; the
observation periods are event-date proxies. This is a focused mapping pilot,
not a complete CDM instance or a clinical analysis dataset.

Generate a graphical QC report after mapping:

```bash
python3 plot_ukb_qc.py \
  --source data/ukb_e11_pilot/ukb_subset.tsv \
  --omop data/ukb_omop_e11_pilot/all \
  --output data/ukb_omop_e11_pilot/qc
```

The script writes `data/ukb_omop_e11_pilot/qc/ukb_omop_qc.png` and a PDF copy.
It shows E11 field coverage and subcodes, BMI and blood-pressure distributions,
the E11 source-to-OMOP flow, and the cohort's sex distribution.
Values outside the pilot QC ranges are counted but omitted from the plotted
histograms so extreme synthetic values do not hide the main distribution.

Evaluate how faithfully the pilot rules transformed the source and how much
was mapped to standard concepts:

```bash
python3 evaluate_ukb_mapping.py \
  --source data/ukb_e11_pilot/ukb_subset.tsv \
  --omop data/ukb_omop_e11_pilot/all \
  --vocabulary /path/to/athena_vocabulary \
  --output data/ukb_omop_e11_pilot/qc \
  --e11-only
```

This checks source-to-output records, row IDs and person links, the selected
Athena concepts, the event-date observation-period proxy, concept coverage,
and out-of-range values. It also counts how many nonempty source BMI, blood
pressure and diagnosis values received a standard concept, were retained with
concept 0, or were not exported. It writes `mapping_evaluation.md` and
`mapping_evaluation.json` in `data/ukb_omop_e11_pilot/qc/` and exits with an error
if a rule-fidelity or structural check fails. A passing result does not
establish clinical mapping accuracy; that needs a reviewed UK ICD-10 mapping
and an independent reference.

UK Biobank distributes complete field-group files, so downloading them may be
large even though the final extract is small. The subset script reports any
missing field IDs if the downloaded files are incomplete.

During a download, a `.part` file is temporary. Leave the command running until it prints `Downloaded and verified` for each file and returns to the shell prompt. Completed files are checked against UKB's MD5 list.

The official synthetic values are randomly generated and can be clinically inconsistent. Use this dataset to test parsing, field selection, joins, and mapping mechanics; report clinical plausibility separately. [UKB synthetic dataset notes](https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html)
