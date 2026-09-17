# Data contract: Synthea to OMOP handover from Subgroup 1 to Subgroup 2

Version: 2026-09-17 (revision 2, based on the first raw Synthea extract)

## 1. The contract fixes the handover for the prototype

Subgroup 1 delivers four OMOP tables as CSV files, and Subgroup 2 builds the NVFlare dataloader against the columns and concept IDs in this contract. The first raw extract contains only patients with type 2 diabetes, so the prototype now fits a linear regression that predicts HbA1c from age, sex, body mass index and systolic blood pressure. Both subgroups must agree before anyone changes the contract.

## 2. Both subgroups use the same fixed versions

| Component | Fixed choice | Reason |
| --- | --- | --- |
| Synthea | 3.3.0 | [ETL-Synthea](https://github.com/OHDSI/ETL-Synthea) supports Synthea versions up to 3.3.0. |
| OMOP CDM | 5.4 | ETL-Synthea supports version 5.4. |
| Reference ETL | OHDSI ETL-Synthea | OHDSI maintains ETL-Synthea as the standard conversion of Synthea output. |
| Vocabularies | One Athena download, with the release date recorded here | ETL-Synthea needs the full vocabulary tables, while the prototype ETL needs only the concept IDs in section 6. |

The team records the Athena release date here: *to be filled in*.

## 3. The first extract holds 98 diabetic patients from one Synthea run

The team profiled the three raw files on 2026-09-17 and found the following content.

| File | Rows | Content relevant to the contract |
| --- | --- | --- |
| patients.csv | 98 | Every patient lives in Massachusetts, 53 patients are female, and 18 patients have a death date. |
| observations.csv | 1,190 | Every patient has at least one HbA1c, body mass index and systolic blood pressure value, and every patient was 21 years or older at each observation. |
| conditions.csv | 98 | Every row records type 2 diabetes (SNOMED 44054006), and no row has a stop date. |

The extract has three consequences for the contract:

- Every patient has type 2 diabetes, so a diabetes label would equal 1 for everyone. The contract therefore uses HbA1c as the outcome and uses the diagnosis to define the cohort.
- The extract lacks `encounters.csv`, so the prototype ETL derives the observation period from the observation and condition dates.
- ETL-Synthea reads the complete Synthea CSV output, including encounters. The team must therefore run ETL-Synthea on the full, unfiltered output of the same Synthea run and filter the reference tables afterwards.

The prototype uses one Synthea run and splits the converted persons into three sites. The split script assigns each person to site_a, site_b or site_c by the remainder of `person_id` divided by 3.

## 4. Subgroup 1 hands over one folder per site

The prototype ETL converts the whole run into `omop/all/`, the validation compares `omop/all/` with `reference/all/`, and the split script then writes the site folders. Subgroup 2 points each NVFlare client at one site folder.

```
raw/run_01/csv/          Synthea output, including the files the prototype ignores
omop/all/                prototype ETL output for the whole run
omop/site_a/             output of the split script for Subgroup 2
omop/site_b/
omop/site_c/
    person.csv
    observation_period.csv
    measurement.csv
    condition_occurrence.csv
reference/all/           the same four tables extracted from ETL-Synthea
```

Every CSV file follows five rules:

- Each file uses commas as separators, UTF-8 encoding and one header row.
- Each file contains exactly the columns of section 5, in lowercase and in the listed order.
- Each date uses the format YYYY-MM-DD.
- An empty field represents a missing value.
- Each file contains only the rows that section 5 selects.

## 5. The ETL fills four tables and 25 columns

The ETL assigns every `_id` key as a sequential integer that starts at 1. The ETL writes one `person` row for every row in `patients.csv`.

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| person_id | integer | patients.Id | The ETL numbers the patients in file order. |
| gender_concept_id | integer | patients.GENDER | The ETL maps M to 8507 and F to 8532. |
| year_of_birth | integer | patients.BIRTHDATE | The ETL takes the year of the birth date. |
| race_concept_id | integer | patients.RACE | The ETL maps the four race values with section 6. |
| ethnicity_concept_id | integer | patients.ETHNICITY | The ETL maps the two ethnicity values with section 6. |
| person_source_value | text | patients.Id | The ETL copies the Synthea patient ID, which links each person to the reference output. |

The ETL writes one `observation_period` row for every patient.

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| observation_period_id | integer | none | The ETL numbers the rows. |
| person_id | integer | observations.PATIENT, conditions.PATIENT | The ETL looks up the patient in `person`. |
| observation_period_start_date | date | observations.DATE, conditions.START | The ETL takes the earliest date across both files. |
| observation_period_end_date | date | observations.DATE, conditions.START | The ETL takes the latest date across both files. |
| period_type_concept_id | integer | none | The ETL writes 32817. |

The ETL writes one `measurement` row for every row in `observations.csv` whose CODE equals 4548-4, 39156-5 or 8480-6.

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| measurement_id | integer | none | The ETL numbers the rows. |
| person_id | integer | observations.PATIENT | The ETL looks up the patient in `person`. |
| measurement_concept_id | integer | observations.CODE | The ETL maps the three LOINC codes with section 6. |
| measurement_date | date | observations.DATE | The ETL keeps the first ten characters of the timestamp. |
| measurement_type_concept_id | integer | none | The ETL writes 32817. |
| value_as_number | decimal | observations.VALUE | The ETL converts the text value to a number. |
| unit_concept_id | integer | observations.UNITS | The ETL maps the three unit strings with section 6. |
| measurement_source_value | text | observations.CODE | The ETL copies the LOINC code. |

The ETL writes one `condition_occurrence` row for every row in `conditions.csv` whose CODE equals 44054006.

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| condition_occurrence_id | integer | none | The ETL numbers the rows. |
| person_id | integer | conditions.PATIENT | The ETL looks up the patient in `person`. |
| condition_concept_id | integer | conditions.CODE | The ETL maps 44054006 to 201826. |
| condition_start_date | date | conditions.START | The ETL copies the start date. |
| condition_type_concept_id | integer | none | The ETL writes 32817. |
| condition_source_value | text | conditions.CODE | The ETL copies the SNOMED code. |

The ETL ignores all other raw columns and codes. The prototype leaves out the death dates, the other laboratory results (glucose, creatinine and eGFR) and all personal identifiers such as names, addresses and social security numbers.

## 6. The prototype uses 16 concept IDs

The ETL hard-codes the following concept IDs, and the team confirms each ID in [Athena](https://athena.ohdsi.org) before coding.

| Meaning | Source value | concept_id | Target column |
| --- | --- | --- | --- |
| Male | M | 8507 | gender_concept_id |
| Female | F | 8532 | gender_concept_id |
| White | white | 8527 | race_concept_id |
| Black or African American | black | 8516 | race_concept_id |
| Asian | asian | 8515 | race_concept_id |
| Native Hawaiian or Other Pacific Islander | hawaiian | 8557 | race_concept_id |
| Hispanic or Latino | hispanic | 38003563 | ethnicity_concept_id |
| Not Hispanic or Latino | nonhispanic | 38003564 | ethnicity_concept_id |
| Hemoglobin A1c | LOINC 4548-4 | 3004410 | measurement_concept_id |
| Body mass index | LOINC 39156-5 | 3038553 | measurement_concept_id |
| Systolic blood pressure | LOINC 8480-6 | 3004249 | measurement_concept_id |
| Percent | % | 8554 | unit_concept_id |
| Kilogram per square metre | kg/m2 | 9531 | unit_concept_id |
| Millimetre of mercury | mm[Hg] | 8876 | unit_concept_id |
| Type 2 diabetes mellitus | SNOMED 44054006 | 201826 | condition_concept_id |
| EHR record | none | 32817 | every type concept column |

The ETL writes 0 for any race or ethnicity value that a later extract adds and section 6 does not list.

## 7. The dataloader builds one row per person with all three measurements

The dataloader of Subgroup 2 reads only the four OMOP files of its site and produces six columns per person.

| Output column | Rule |
| --- | --- |
| person_id | The dataloader copies `person.person_id`. |
| male | The dataloader writes 1 when `gender_concept_id` equals 8507 and 0 otherwise. |
| age | The dataloader subtracts `year_of_birth` from the year of the earliest HbA1c measurement. |
| bmi | The dataloader takes the earliest body mass index value. |
| sbp | The dataloader takes the earliest systolic blood pressure value. |
| hba1c | The dataloader takes the earliest HbA1c value as the regression target. |

The dataloader keeps only persons with a `condition_occurrence` row for concept 201826 and at least one value for each measurement. All 98 persons in the first extract meet both conditions, and 91 persons have all three earliest values on the same date. The first HbA1c values range from 6.0% to 9.1%, so the target varies little, which the prototype accepts because the README does not require accuracy.

## 8. A site passes when all checks succeed

The validation script runs four structural checks on `omop/all/` and on each site folder:

1. The script confirms that the four files exist with the columns of section 5 in the listed order.
2. The script confirms that every `person_id` in the other three tables exists in `person`.
3. The script confirms that every concept column contains only the values of section 6.
4. The script confirms that HbA1c lies between 3 and 20, body mass index between 10 and 80 and systolic blood pressure between 60 and 250.

The script then compares `omop/all/` with `reference/all/`. The two ETLs number their keys differently, so the script joins persons on `person_source_value` and filters the reference tables to the persons of the extract and the concepts of section 6.

| Table | Compared values |
| --- | --- |
| person | The script compares the set of patients, `gender_concept_id`, `year_of_birth`, `race_concept_id` and `ethnicity_concept_id`. |
| measurement | The script compares the row count per person and concept, and the values on each date within 0.01. |
| condition_occurrence | The script compares the set of persons with concept 201826 and their start dates. |

The script leaves `observation_period`, the type concept columns and all `_id` keys out of the comparison, because ETL-Synthea derives those values from encounters and its own rules. The team documents every remaining difference and decides whether the prototype ETL or the reference causes the difference.
