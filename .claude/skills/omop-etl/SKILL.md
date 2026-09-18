---
name: omop-etl
description: Convert a raw tabular health data source (CSV files, one folder per site) into the four OMOP tables of contract/data_contract.md (person, observation_period, measurement, condition_occurrence) using DuckDB. Use when someone wants to turn Synthea or another source dataset into the OMOP CSVs that subproject 2 (federated learning) reads, or asks to profile, map, convert, or validate source data for OMOP.
---

# OMOP ETL skill (MVP)

Turns raw source CSVs into the OMOP tables of `contract/data_contract.md`, one folder per site. Subproject 2 reads these folders.

Existing software does the heavy lifting. DuckDB profiles the source and runs the mapping, pandas checks the result. Your part is the step no tool automates: writing the mapping.

## Reference

- [OMOP Common Data Model documentation (OHDSI)](https://ohdsi.github.io/CommonDataModel/)
- [OMOP CDM v5.4 tables and fields](https://ohdsi.github.io/CommonDataModel/cdm54.html), the version the data contract uses
- [Athena](https://athena.ohdsi.org/), the OMOP standardized vocabularies, to look up and confirm concept IDs
- [The Book of OHDSI, ETL chapter](https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html)

When `contract.yaml` doesn't answer a mapping question, look up the table and field definitions in the CDM v5.4 documentation instead of relying on memory.

## Inputs

- A folder of raw `.csv` or `.csv.gz` files per site, for example `synthea_cohorts/cohort_2/data/source_compact/site_a/`
- `omop_skill/contract.yaml`, the machine-readable data contract
- Existing mappings in `omop_skill/mappings/`, as examples

## Steps

1. **Profile the source.**
   `python omop_skill/scripts/profile_source.py --source <site_csv_dir> --out omop_skill/work/profile_<site>.md`
   Read the report: tables, columns, empty values, the most frequent codes with their descriptions.

2. **Pick or write the mapping.**
   - Synthea 3.3.0 under the current contract: use `omop_skill/mappings/synthea_3.3.0_contract_rev3.yaml` as is.
   - Revision 1 data such as `cohort_2`: `omop_skill/mappings/synthea_3.3.0.yaml`.
   - UK Biobank wide extract: `omop_skill/mappings/ukb_pilot.yaml`.
   - Any other source: copy it to `omop_skill/mappings/<source>.yaml`, then rewrite `files`, `concept_maps`, and every table from the profile.

   Rules for a mapping:
   - Output columns, their order, and their types come from `contract.yaml`. Don't add, drop, or rename columns.
   - Use only concept IDs listed in `contract.yaml`. If a source code has no confirmed concept ID, leave it out and name it in the report.
   - Expressions are DuckDB SQL. Source tables are named after the keys under `files`, and each has a `src_row` column with the row number in the source file.
   - Build `person` first. Later tables join it as `omop_person` to look up `person_id`.
   - Helpers: `omop_concept('<map>', value)` returns the concept ID, `omop_codes('<map>')` lists the source codes of a map, `omop_date(value)` turns a date or timestamp into `YYYY-MM-DD` in UTC.
   - Number IDs with `row_number() OVER (ORDER BY ...)` so the output is reproducible.

3. **Run the mapping for every site.**
   `python omop_skill/scripts/run_mapping.py --mapping <mapping.yaml> --source <site_csv_dir> --out <output_dir>/<site>`

4. **Validate.**
   `python omop_skill/scripts/validate_contract.py <output_dir>/<site>`
   `contract.yaml` is the current contract (2026-09-18). For revision 1 data such as `cohort_2`, add `--contract omop_skill/contract_rev1.yaml`.
   Then run the quality report for completeness, date logic and mapping rate:
   `python omop_skill/scripts/qc_report.py --output <output_dir>/<site> --out <output_dir>/<site>/qc_report.md --json <output_dir>/<site>/qc.json`
   If a trusted output with the same IDs exists, for example `synthea_cohorts/cohort_2/data/omop/<site>`, add `--reference <that_dir>`.

5. **Split and compare, when the contract asks for it.**
   - One run into sites: `python omop_skill/scripts/split_sites.py <output_dir>/all <output_dir>`
   - Against a reference conversion of the same raw data, such as ETL-Synthea: `python omop_skill/scripts/compare_reference.py <output_dir>/all <reference_dir>/all`. Persons are joined on `person_source_value`, so the IDs may differ.

6. **Fix and repeat.** If a check fails because of the mapping, fix the mapping and rerun steps 3 to 5. Stop after three attempts and report what still fails. If it fails because of the data, for example implausible values in the source, report it instead of bending the mapping.

7. **Report** per site: row counts per table, the validation result, and source codes from the profile that the mapping leaves out.

A person reviews every new mapping before its output replaces data that subproject 2 uses.
