# Multi-site diabetes cohort

Five sites for the federated prototype, following the OMOP tables in [`contract/data_contract.md`](../../contract/data_contract.md).
Each site is one Synthea population and one NVFlare client, so no site sees another's patients.

```bash
python build_datasets.py
```

The script downloads Synthea v3.3.0, generates one population per site, writes the contract tables and a compact copy of the source.
Every site has a fixed seed, so re-running reproduces the same data.

## Layout

```
data/omop/<site>/             the four contract tables
    person.csv
    observation_period.csv
    measurement.csv
    condition_occurrence.csv
data/source_compact/<site>/   filtered Synthea source, gzipped
data/source/<site>/csv/       full Synthea export, not tracked in git
```

The tracked tables and compact source total 16 MB.
The full export is 3.9 GB, so it is gitignored; `build_datasets.py` regenerates it.
`data/source_compact/manifest.json` records each site's parameters, row counts and sha256.

## Sites

Sites differ in age range, size and gender, so T2DM prevalence spans a factor of sixteen.
That is what makes the federation non-IID, and age is the lever doing most of the work.

| site | state | ages | gender | persons | T2DM |
| --- | --- | --- | --- | --- | --- |
| site_a | Illinois | 18-40 | M+F | 1235 | 1.1% |
| site_b | Washington | 35-65 | M+F | 1000 | 6.3% |
| site_c | Arizona | 65-95 | M+F | 1128 | 17.4% |
| site_d | Colorado | 40-80 | M+F | 507 | 12.8% |
| site_e | Oregon | 25-60 | F | 744 | 7.0% |

Person counts exceed the requested population because Synthea also writes deceased patients.
Site sizes differ deliberately, so FedAvg has to weight clients unequally.

## Contract compliance

`build_datasets.py` writes the columns of contract sections 4.1 to 4.4 in the listed order and lowercase, with dates as `YYYY-MM-DD`.
Measurements cover LOINC 39156-5 and 8480-6, conditions SNOMED 44054006, as section 5 selects.
Concept IDs are the hardcoded values of section 5, so no Athena vocabulary download is involved.

Two points to confirm with Subgroup 2.
Section 3 specifies one site of 100 patients, whereas this builds five sites of 400 to 1200.
The `reference/` folder of section 4, holding the same tables from OHDSI ETL-Synthea, does not exist yet.
