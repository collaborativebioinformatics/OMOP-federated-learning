# Synthea biomarker and mortality cohort

This pipeline **uses Synthea output**; it does not call Synthea or change its disease
modules. Synthea generates longitudinal patient records, and `build_cohort.py` reads
three of its relational CSV files:

- `patients.csv` for demographics and death date
- `conditions.csv` for type 2 diabetes diagnoses (SNOMED `44054006`)
- `observations.csv` for LOINC-coded biomarkers

It writes normalized, linkable tables rather than combining diagnoses and biomarkers:

- `patients.csv`: one row per eligible patient, including mortality/censoring
- `diagnoses.csv`: one row per type 2 diabetes diagnosis event
- `biomarkers.csv`: one row per numeric biomarker measurement in the index window

All tables join on `patient_id`. Diagnosis and biomarker rows also preserve Synthea's
`encounter_id`.

## Generate source data

From a Synthea checkout (Java 17+):

```bash
./run_synthea -s 20260917 -p 10000 Massachusetts \
  --exporter.csv.export=true \
  --exporter.fhir.export=false \
  --exporter.years_of_history=0
```

## Build the tables

```bash
python3 synthea_cohort/build_cohort.py \
  --input /path/to/synthea/output/csv \
  --output synthea_cohort/output \
  --study-end 2026-12-31 \
  --window-days 365
```

The biomarker table contains every supported numeric measurement within 365 days
before or after the first diabetes diagnosis. `days_from_index` is negative before
diagnosis, zero on diagnosis, and positive afterward.

## OMOP concept IDs

The source SNOMED/LOINC codes and OMOP concept IDs are both retained. OMOP concept IDs
belong to a particular vocabulary release, so for reproducible work export
`CONCEPT.csv` from your licensed OHDSI Athena vocabulary bundle and pass it in:

```bash
python3 synthea_cohort/build_cohort.py \
  --input /path/to/synthea/output/csv \
  --output synthea_cohort/output \
  --study-end 2026-12-31 \
  --omop-concepts /path/to/athena/CONCEPT.csv
```

The file may be comma- or tab-delimited. Only valid standard (`standard_concept=S`)
SNOMED and LOINC concepts are used. A small verified fallback map is bundled; codes
not present in either source receive concept ID `0`, OMOP's unmapped convention.

For a complete OMOP CDM database rather than these research-friendly CSV tables, use
OHDSI's ETL-Synthea package, which converts Synthea CSV into OMOP CDM 5.3/5.4.
