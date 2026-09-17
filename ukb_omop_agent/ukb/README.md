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
python3 ukb_omop_agent/ukb/sample_ukb_fields.py \
  --rows 10000 \
  --output ukb_omop_agent/ukb/data/ukb_sampled

python3 ukb_omop_agent/ukb/make_ukb_subset.py \
  --input ukb_omop_agent/ukb/data/ukb_sampled \
  --output ukb_omop_agent/ukb/data/ukb_e11_pilot \
  --all-e11
```

The sampler takes the first 10,000 records from each relevant field-group file and verifies that their EIDs align. Because the files are partial extracts, the published whole-file MD5 checksums cannot verify them. The subset command selects every participant with an E11-family code in that sample; the number selected depends on the downloaded data.

The result is `ukb_omop_agent/ukb/data/ukb_e11_pilot/ukb_subset.tsv` plus a `manifest.json`. The TSV has one row per selected EID and retains available instances and arrays for the seven fields. It is source data, **not yet OMOP**.

## Download complete field-group files instead

If you need the complete synthetic field-group files, first inspect the download plan, then download and verify the published MD5 checksums:

```bash
python3 ukb_omop_agent/ukb/download_ukb_fields.py
python3 ukb_omop_agent/ukb/download_ukb_fields.py \
  --download --output ukb_omop_agent/ukb/data/ukb_tabular

python3 ukb_omop_agent/ukb/make_ukb_subset.py \
  --input ukb_omop_agent/ukb/data/ukb_tabular \
  --output ukb_omop_agent/ukb/data/ukb_e11_pilot \
  --all-e11
```

Complete files can be large. During download, `.part` files are temporary; a completed file is reported as downloaded and verified. The subset script reports missing required fields if the input files are incomplete.

Next, follow the [general agent workflow](../README.md) to inspect Athena candidates, review them, apply mappings, and run QC. UK Biobank warns that its synthetic values are randomly generated and may be internally inconsistent, so this dataset tests processing and mapping mechanics rather than clinical plausibility.
