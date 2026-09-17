# Multi-site OMOP datasets for the federated prototype

Five datasets, each a federation of two to five sites, following the OMOP tables in [`contract/data_contract.md`](../../contract/data_contract.md).
Each site is one Synthea population and one NVFlare client, so sites never pool data.
The datasets vary in site count so you can test how the federation behaves as clients are added.

```bash
python build_datasets.py
```

The script downloads Synthea v3.3.0, generates one population per site and writes the contract tables.
Every site has a fixed seed, so re-running reproduces the same data.

## Layout

```
<dataset>/raw/<site>/csv/    Synthea export, not tracked in git
<dataset>/omop/<site>/       the four contract tables
    person.csv
    observation_period.csv
    measurement.csv
    condition_occurrence.csv
```

The tracked `omop/` tables total 32 MB across all five datasets.
The `raw/` exports total 15 GB, so they are gitignored; `build_datasets.py` regenerates them.

## Datasets

Sites within a dataset use different US states and seeds, so their cohorts differ in demographics, not just in sampling.

| dataset | sites | states | persons | T2DM patients |
| --- | --- | --- | --- | --- |
| dataset_1 | 2 | Massachusetts, California | 2316 | 152 |
| dataset_2 | 3 | Texas, New York, Florida | 3494 | 248 |
| dataset_3 | 4 | Pennsylvania, Ohio, Georgia, Michigan | 4707 | 323 |
| dataset_4 | 5 | Illinois, Washington, Arizona, Colorado, Oregon | 5773 | 432 |
| dataset_5 | 3 | Alabama, Minnesota, Utah | 3423 | 223 |

Person counts exceed the 1000 requested per site because Synthea also writes deceased patients.

T2DM prevalence lands between 6.5% and 7.5% in every dataset, so the choice of state buys little heterogeneity.
To make the split more non-IID, vary the population parameters per site instead of the state.

## Contract compliance

`build_datasets.py` writes the columns of contract sections 4.1 to 4.4 in the listed order and lowercase, with dates as `YYYY-MM-DD`.
Measurements cover LOINC 39156-5 and 8480-6, conditions SNOMED 44054006, as section 5 selects.
Concept IDs are the hardcoded values of section 5, so no Athena vocabulary download is involved.

Two things deviate from the contract; confirm both with Subgroup 2.
Section 3 specifies one site of 100 patients, whereas this generates 17 sites of 1000 across five federations.
The `reference/` folder of section 4, holding the same tables from OHDSI ETL-Synthea, does not exist yet.
