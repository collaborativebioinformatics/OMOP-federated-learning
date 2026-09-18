# Synthea biomarker and mortality cohort

This pipeline **uses Synthea output**; it does not call Synthea or change its disease modules.
Synthea generates longitudinal patient records, and `build_cohort.py` reads three of its relational CSV files:

- `patients.csv` for demographics and death date
- `conditions.csv` for type 2 diabetes diagnoses (SNOMED `44054006`)
- `observations.csv` for LOINC-coded biomarkers

It writes normalized, linkable tables rather than combining diagnoses and biomarkers:

- `patients.csv`: one row per eligible patient, including mortality/censoring
- `diagnoses.csv`: one row per type 2 diabetes diagnosis event
- `biomarkers.csv`: one row per numeric biomarker measurement in the index window

All tables join on `patient_id`.
Diagnosis and biomarker rows also preserve Synthea's `encounter_id`.

## Generate source data

From a Synthea checkout (Java 17+):

```bash
./run_synthea -s 20260917 -p 10000 Massachusetts \
  --exporter.csv.export=true \
  --exporter.fhir.export=false \
  --exporter.years_of_history=0
```

## Data provenance

The data in this cohort was prepared in three stages:

1. The command above generated the raw Synthea `patients.csv`, `conditions.csv`, and
   `observations.csv` files for Massachusetts using random seed `20260917`.
2. `build_cohort.py` selected patients with type 2 diabetes (SNOMED `44054006`) and
wrote separate patient, diagnosis, and biomarker tables.
Biomarkers are numeric measurements for the configured LOINC codes within 365 days before or after each patient's first diabetes diagnosis.
3. `compact_source.py` retained only the 98 cohort patients and their relevant source
   rows, then wrote deterministic gzip archives under `data/source_compact/` so the source subset could be stored in Git.

The project data contract specifies Synthea version `3.3.0`.
Synthea's CSV output does not embed its generator version or the command used, however, so the archived CSV files cannot independently prove that provenance.
When regenerating the data, record the Synthea Git tag or commit alongside the command; from the Synthea checkout it can be reported with `git describe --tags --always`.

## Build the tables

```bash
python3 synthea_cohorts/cohort_1/build_cohort.py \
  --input /path/to/synthea/output/csv \
  --output synthea_cohorts/cohort_1/output \
  --study-end 2026-12-31 \
  --window-days 365
```

The biomarker table contains every supported numeric measurement within 365 days before or after the first diabetes diagnosis.
`days_from_index` is negative before diagnosis, zero on diagnosis, and positive afterward.

## OMOP concept IDs

The source SNOMED/LOINC codes and OMOP concept IDs are both retained.
OMOP concept IDs belong to a particular vocabulary release, so for reproducible work export `CONCEPT.csv` from your licensed OHDSI Athena vocabulary bundle and pass it in:

```bash
python3 synthea_cohorts/cohort_1/build_cohort.py \
  --input /path/to/synthea/output/csv \
  --output synthea_cohorts/cohort_1/output \
  --study-end 2026-12-31 \
  --omop-concepts /path/to/athena/CONCEPT.csv
```

The file may be comma- or tab-delimited.
Only valid standard (`standard_concept=S`) SNOMED and LOINC concepts are used.
A small verified fallback map is bundled; codes not present in either source receive concept ID `0`, OMOP's unmapped convention.

For a complete OMOP CDM database rather than these research-friendly CSV tables, use OHDSI's ETL-Synthea package, which converts Synthea CSV into OMOP CDM 5.3/5.4.

## Compact source archive for Git

The complete Synthea export is intentionally ignored because `observations.csv` alone is about 345 MB. Create a reproducible, cohort-specific source archive with:

```bash
python3 synthea_cohorts/cohort_1/compact_source.py
```

This writes tracked-size gzip files to `synthea_cohorts/cohort_1/data/source_compact/`.
They retain the original Synthea columns and row order, but contain only cohort patients, type-2-diabetes conditions, and the numeric configured biomarkers inside the ±365-day diagnosis window.

To reconstruct ordinary CSV inputs and rebuild the cohort:

```bash
mkdir -p /tmp/synthea-compact
gzip -dc synthea_cohorts/cohort_1/data/source_compact/patients.csv.gz > /tmp/synthea-compact/patients.csv
gzip -dc synthea_cohorts/cohort_1/data/source_compact/conditions.csv.gz > /tmp/synthea-compact/conditions.csv
gzip -dc synthea_cohorts/cohort_1/data/source_compact/observations.csv.gz > /tmp/synthea-compact/observations.csv
python3 synthea_cohorts/cohort_1/build_cohort.py \
  --input /tmp/synthea-compact --output /tmp/rebuilt-cohort \
  --study-end 2026-09-17 --window-days 365
```

`manifest.json` records row counts, filters, compressed sizes, and SHA-256 hashes.
