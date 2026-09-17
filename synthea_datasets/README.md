# Synthea datasets for the federated prototype

Five simulated sites, each a separate Synthea population mapped to the OMOP tables of [`contract/data_contract.md`](../contract/data_contract.md).
Every site is one NVFlare client: the sites never pool data, so each folder under `omop/` is a self-contained input.

```bash
python build.py
```

The script downloads Synthea v3.3.0, generates one population per site and writes the contract tables.
Re-running reproduces the same data, since each site has a fixed seed.

## Layout

```
raw/<site>/csv/          Synthea export, not tracked in git
omop/<site>/             the four contract tables
    person.csv
    observation_period.csv
    measurement.csv
    condition_occurrence.csv
```

`raw/` is 4.1 GB and reproducible from `build.py`, so it is gitignored.
`omop/` is 9.3 MB and tracked, because it is the handover artifact Subgroup 2 builds against.

## Sites

Each site is a different US state and seed, so the cohorts differ in demographics rather than only in sampling.

| site | state | seed | persons | measurements | T2DM patients | prevalence |
| --- | --- | --- | --- | --- | --- | --- |
| site_a | Massachusetts | 101 | 1153 | 34495 | 85 | 7.4% |
| site_b | California | 102 | 1163 | 33774 | 67 | 5.8% |
| site_c | Texas | 103 | 1121 | 32052 | 67 | 5.8% |
| site_d | New York | 104 | 1162 | 33733 | 65 | 5.8% |
| site_e | Florida | 105 | 1167 | 37591 | 68 | 5.8% |

Person counts exceed the 1000 requested because Synthea also writes deceased patients.

The state differences move demographics only mildly, and outcome prevalence is nearly flat across sites.
If the federation needs a stronger non-IID signal, vary the population parameters per site rather than the state alone.

## Contract compliance

`build.py` writes exactly the columns of contract sections 4.1 to 4.4, in the listed order and lowercase, with dates as `YYYY-MM-DD`.
Measurements are restricted to LOINC 39156-5 and 8480-6, and conditions to SNOMED 44054006, as section 5 selects.
All concept IDs are the hardcoded values of section 5; no vocabulary download is involved.

Two deviations from the contract as written are worth confirming with Subgroup 2.
Section 3 specifies a single site of 100 patients, whereas this generates five sites of 1000 to give the federation something to average over.
The `reference/` folder of section 4, holding the same tables from OHDSI ETL-Synthea, is not yet produced.
