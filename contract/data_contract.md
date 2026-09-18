# Data contract

## 1. Versions

| Component | Fixed choice | Reason |
| --- | --- | --- |
| Synthea | 3.3.0 | [ETL-Synthea](https://github.com/OHDSI/ETL-Synthea) supports Synthea versions up to 3.3.0. |
| OMOP CDM | 5.4 | ETL-Synthea supports version 5.4. |
| Reference ETL | OHDSI ETL-Synthea | OHDSI maintains ETL-Synthea as the standard conversion of Synthea output. |
| Vocabularies | One Athena download, with the release date recorded here | ETL-Synthea needs the full vocabulary tables, and the prototype ETL needs only the concept identifiers in section 6. |

## 2. Tables

### patients

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| person_id | integer | patients.Id | The ETL numbers the patients in file order. |
| gender_concept_id | integer | patients.GENDER | The ETL maps M to 8507 and F to 8532. |
| year_of_birth | integer | patients.BIRTHDATE | The ETL takes the year of the birth date. |
| race_concept_id | integer | patients.RACE | The ETL maps the race values with section 6. |
| ethnicity_concept_id | integer | patients.ETHNICITY | The ETL maps the ethnicity values with section 6. |
| person_source_value | text | patients.Id | The ETL copies the Synthea patient identifier, which links each person to the reference output. |

### observation_period

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| observation_period_id | integer | none | The ETL numbers the rows. |
| person_id | integer | observations.PATIENT, conditions.PATIENT | The ETL looks up the patient in `person`. |
| observation_period_start_date | date | observations.DATE, conditions.START | The ETL takes the earliest date across both files. |
| observation_period_end_date | date | observations.DATE, conditions.START | The ETL takes the latest date across both files. |
| period_type_concept_id | integer | none | The ETL writes 32817. |

### measurement

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| measurement_id | integer | none | The ETL numbers the rows. |
| person_id | integer | observations.PATIENT | The ETL looks up the patient in `person`. |
| measurement_concept_id | integer | observations.CODE | The ETL maps the LOINC code with section 6. |
| measurement_date | date | observations.DATE | The ETL keeps the first ten characters of the timestamp. |
| measurement_type_concept_id | integer | none | The ETL writes 32817. |
| value_as_number | decimal | observations.VALUE | The ETL converts the text value to a number. |
| unit_concept_id | integer | observations.UNITS | The ETL maps the unit string with section 6. |
| measurement_source_value | text | observations.CODE | The ETL copies the LOINC code. |

### condition_occurrence

| Column | Type | Synthea source | Rule |
| --- | --- | --- | --- |
| condition_occurrence_id | integer | none | The ETL numbers the rows. |
| person_id | integer | conditions.PATIENT | The ETL looks up the patient in `person`. |
| condition_concept_id | integer | conditions.CODE | The ETL maps the SNOMED code with section 6. |
| condition_start_date | date | conditions.START | The ETL copies the start date. |
| condition_type_concept_id | integer | none | The ETL writes 32817. |
| condition_source_value | text | conditions.CODE | The ETL copies the SNOMED code. |

## 2. Concepts

### Conditions

| Meaning | Source code | concept_id | Status |
| --- | --- | --- | --- |
| Type 2 diabetes mellitus | SNOMED 44054006 | 201826 | The code appears in the first extract. |
| Alzheimer disease | SNOMED 26929004 | 378419 | The code is absent from the first extract. |
| Parkinson disease | SNOMED 49049000 | 381270 | The code is absent from the first extract. |

### Measurements

| Meaning | Source code | concept_id | Unit in Synthea |
| --- | --- | --- | --- |
| Hemoglobin A1c | LOINC 4548-4 | 3004410 | % |
| Body mass index | LOINC 39156-5 | 3038553 | kg/m2 |
| Systolic blood pressure | LOINC 8480-6 | 3004249 | mm[Hg] |
| Glucose in blood | LOINC 2339-0 | 3000483 | mg/dL |
| Glucose in serum or plasma | LOINC 2345-7 | 3004501 | mg/dL |
| Total cholesterol | LOINC 2093-3 | 3027114 | mg/dL |
| HDL cholesterol | LOINC 2085-9 | 3007070 | mg/dL |
| LDL cholesterol | LOINC 18262-6 | 3009966 | mg/dL |
| Triglycerides | LOINC 2571-8 | 3022192 | mg/dL |

### Demographics and units

| Meaning | Source value | concept_id |
| --- | --- | --- |
| Male | M | 8507 |
| Female | F | 8532 |
| White | white | 8527 |
| Black or African American | black | 8516 |
| Asian | asian | 8515 |
| Native Hawaiian or Other Pacific Islander | hawaiian | 8557 |
| Hispanic or Latino | hispanic | 38003563 |
| Not Hispanic or Latino | nonhispanic | 38003564 |
| Percent | % | 8554 |
| Kilogram per square metre | kg/m2 | 9531 |
| Millimetre of mercury | mm[Hg] | 8876 |
| Milligram per decilitre | mg/dL | 8840 |
| EHR record | none | 32817 |

The ETL writes 0 for any race or ethnicity value that a later extract adds and section 6 does not list. The ETL maps the two glucose codes to their own concepts and never merges the two codes into one concept.

