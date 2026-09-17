# Data contract: Synthea to OMOP handover from Subgroup 1 to Subgroup 2

Version: 2026-09-17

## 1. Overview

Subgroup 1 delivers four OMOP tables as CSV files for each simulated site, and Subgroup 2 builds the NVFlare dataloader against the columns and concept IDs in this contract. The prototype uses Synthea data only and predicts type 2 diabetes from age, sex, body mass index and systolic blood pressure. Both subgroups must agree before anyone changes the contract.

## 2. Versions

| Component | Fixed choice | Reason |
| --- | --- | --- |
| Synthea | 3.3.0 | [ETL-Synthea](https://github.com/OHDSI/ETL-Synthea) supports Synthea versions up to 3.3.0. |
| OMOP CDM | 5.4 | ETL-Synthea supports version 5.4, and the team no longer depends on the Eunomia files in version 5.3. |
| Reference ETL | OHDSI ETL-Synthea | OHDSI maintains ETL-Synthea as the standard conversion of Synthea output. |
| Vocabularies | One Athena download, with the release date recorded here | ETL-Synthea needs the full vocabulary tables, while the prototype ETL needs only the concept IDs in section 6. |

## 3. Synthetic Patients

For a proof of concept, we generate 100 synthetic patients in Synthea.

| Site | Seed | State | Living patients |
| --- | --- | --- | --- |
| site_a | 101 | Massachusetts | 100 |


The prototype ETL reads four raw files: `patients.csv`, `encounters.csv`, `observations.csv` and `conditions.csv`. Synthea also writes deceased patients, so the final patient count exceeds 1000.

## 4. Table Structure

Subgroup 2 points each NVFlare client at one folder under `omop/`. The `reference/` folder holds the same four tables from ETL-Synthea and serves only the validation in section 8.

```
raw/site_a/csv/          Synthea output
omop/site_a/             prototype ETL output for Subgroup 2
    person.csv
    observation_period.csv
    measurement.csv
    condition_occurrence.csv
reference/site_a/        the same four files extracted from ETL-Synthea
```

Every CSV file follows five rules:

- Each file uses commas as separators, UTF-8 encoding and one header row.
- Each file contains exactly the columns of section 5, in lowercase and in the listed order.
- Each date uses the format YYYY-MM-DD.
- An empty field represents a missing value.
- Each file contains only the rows that section 5 selects.

## 4. Selected OMOP Tables

### 4.1 Person

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| person_id | integer | patients.Id | The ETL numbers the patients in file order. |
| gender_concept_id | integer | patients.GENDER | The ETL maps M to 8507 and F to 8532. |
| year_of_birth | integer | patients.BIRTHDATE | The ETL takes the year of the birth date. |
| race_concept_id | integer | none | The ETL writes 0. |
| ethnicity_concept_id | integer | none | The ETL writes 0. |
| person_source_value | text | patients.Id | The ETL copies the Synthea patient ID, which links each person to the reference output. |

### 4.2 Observation_Period

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| observation_period_id | integer | none | The ETL numbers the rows. |
| person_id | integer | encounters.PATIENT | The ETL looks up the patient in `person`. |
| observation_period_start_date | date | encounters.START | The ETL takes the earliest encounter start date. |
| observation_period_end_date | date | encounters.STOP | The ETL takes the latest encounter stop date. |
| period_type_concept_id | integer | none | The ETL writes 32817. |

### 4.3 Measurement

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| measurement_id | integer | none | The ETL numbers the rows. |
| person_id | integer | observations.PATIENT | The ETL looks up the patient in `person`. |
| measurement_concept_id | integer | observations.CODE | The ETL maps 39156-5 to 3038553 and 8480-6 to 3004249. |
| measurement_date | date | observations.DATE | The ETL keeps the date and drops the time. |
| measurement_type_concept_id | integer | none | The ETL writes 32817. |
| value_as_number | decimal | observations.VALUE | The ETL converts the text value to a number. |
| unit_concept_id | integer | observations.UNITS | The ETL maps kg/m2 to 9531 and mm[Hg] to 8876. |
| measurement_source_value | text | observations.CODE | The ETL copies the LOINC code. |

### 4.4 Condition_Occurence

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| condition_occurrence_id | integer | none | The ETL numbers the rows. |
| person_id | integer | conditions.PATIENT | The ETL looks up the patient in `person`. |
| condition_concept_id | integer | conditions.CODE | The ETL maps 44054006 to 201826. |
| condition_start_date | date | conditions.START | The ETL copies the start date. |
| condition_type_concept_id | integer | none | The ETL writes 32817. |
| condition_source_value | text | conditions.CODE | The ETL copies the SNOMED code. |

## 5. Minimal set of concept IDs

The ETL hard-codes the following concept IDs, and the team confirms each ID in [Athena](https://athena.ohdsi.org) before coding.

| Meaning | Source value | concept_id | Target column |
| --- | --- | --- | --- |
| Male | M | 8507 | gender_concept_id |
| Female | F | 8532 | gender_concept_id |
| Body mass index | LOINC 39156-5 | 3038553 | measurement_concept_id |
| Systolic blood pressure | LOINC 8480-6 | 3004249 | measurement_concept_id |
| Kilogram per square metre | kg/m2 | 9531 | unit_concept_id |
| Millimetre of mercury | mm[Hg] | 8876 | unit_concept_id |
| Type 2 diabetes mellitus | SNOMED 44054006 | 201826 | condition_concept_id |
| EHR record | none | 32817 | every type concept column |
| No matching concept | none | 0 | race_concept_id, ethnicity_concept_id |
