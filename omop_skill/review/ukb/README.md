# Prepare synthetic UK Biobank input

These scripts download and select fields from the [official UK Biobank synthetic tabular dataset](https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html). This UKB folder contains **scripts only**; downloaded files and generated extracts stay under its ignored `data/` directory. Run the commands below from the `OMOP-federated-learning` repository root.

| UKB field | Selected value | Use in the OMOP agent |
| --- | --- | --- |
| 31 | Sex | Person |
| 34 | Year of birth | Person |
| 53 | Assessment date | BMI and blood-pressure event date |
| 21001 | Body mass index | Measurement |
| 4080 | Automated systolic blood pressure | Measurement |
| 41270 | ICD-10 diagnosis | Condition candidate |
| 41280 | First diagnosis date | Paired with 41270 by the same instance and array index |

## Download a 10,000-row sample and create the E11 subset

```bash
python3 omop_skill/review/ukb/sample_ukb_fields.py \
  --rows 10000 \
  --output omop_skill/review/ukb/data/ukb_sampled

python3 omop_skill/review/ukb/make_ukb_subset.py \
  --input omop_skill/review/ukb/data/ukb_sampled \
  --output omop_skill/review/ukb/data/ukb_e11_pilot \
  --all-e11
```

The sampler takes the first 10,000 records from each relevant field-group file and verifies that their EIDs align. Because the files are partial extracts, the published whole-file MD5 checksums cannot verify them. The subset command then selects **every participant** with a diagnosis code starting `E11` in that sample, without a fixed participant or case count. It keeps all seven selected fields for those people, including diagnosis codes outside the E11 family. The number selected depends on the downloaded data.

The result is `omop_skill/review/ukb/data/ukb_e11_pilot/ukb_subset.tsv` plus a `manifest.json`. The TSV has one row per selected EID and retains available instances and arrays for the seven fields. It is source data, **not yet OMOP**. Later, `general_agent.py --diagnosis-prefix E11` selects only E11-family diagnosis events for OMOP review and mapping; that flag does not change who is in the TSV.

For a different diagnosis, select participants by exact ICD-10 code or family prefix. For example, to include every participant with a `J45` family code in the 10,000-row sample:

```bash
python3 omop_skill/review/ukb/make_ukb_subset.py \
  --input omop_skill/review/ukb/data/ukb_sampled \
  --output omop_skill/review/ukb/data/ukb_j45_pilot \
  --all-matching --diagnosis-prefix J45
```

`--diagnosis-code J450` selects only that exact code; codes may include a decimal point. Repeat either filter to combine selections. Without `--all-matching`, `--cases` and `--participants` produce a diagnosis-enriched sample; without diagnosis filters, the historical E11 default applies. The manifest records the actual filters and counts. When mapping the result, pass the same diagnosis filter to `general_agent.py inspect`, `apply`, and `qc`. A diagnosis name must first be resolved to the intended ICD-10 code or family using Athena; the subset script does not guess codes from names.

## Download complete field-group files instead

If you need the complete synthetic field-group files, first inspect the download plan, then download and verify the published MD5 checksums:

```bash
python3 omop_skill/review/ukb/download_ukb_fields.py
python3 omop_skill/review/ukb/download_ukb_fields.py \
  --download --output omop_skill/review/ukb/data/ukb_tabular

python3 omop_skill/review/ukb/make_ukb_subset.py \
  --input omop_skill/review/ukb/data/ukb_tabular \
  --output omop_skill/review/ukb/data/ukb_e11_pilot \
  --all-e11
```

Complete files can be large. During download, `.part` files are temporary; a completed file is reported as downloaded and verified. The subset script reports missing required fields if the input files are incomplete.

Next, follow the [general agent workflow](../README.md) to inspect Athena candidates, review them, apply mappings, and run QC. UK Biobank warns that its synthetic values are randomly generated and may be internally inconsistent, so this dataset tests processing and mapping mechanics rather than clinical plausibility.
