---
name: omop-etl
description: Convert a raw tabular health data source (CSV files, one folder per site) into the four OMOP tables of contract/data_contract.md (person, observation_period, measurement, condition_occurrence) using DuckDB. Use when someone wants to turn Synthea or another source dataset into the OMOP CSVs that subproject 2 (federated learning) reads, or asks to profile, map, convert, or validate source data for OMOP.
---

# OMOP ETL skill (MVP)

Turns raw source CSVs into the OMOP tables of `contract/data_contract.md`, one folder per site. Subproject 2 reads these folders.

Existing software does the heavy lifting. DuckDB profiles the source and runs the mapping, pandas checks the result. Your part is the step no tool automates: writing the mapping.

## Inputs

- A folder of raw `.csv` or `.csv.gz` files per site, for example `synthea_cohorts/cohort_2/data/source_compact/site_a/`
- `omop_skill/contract.yaml`, the machine-readable data contract
- Existing mappings in `omop_skill/mappings/`, as examples

## Steps

1. **Profile the source.**
   `python omop_skill/scripts/profile_source.py --source <site_csv_dir> --out omop_skill/work/profile_<site>.md`
   Read the report: tables, columns, empty values, the most frequent codes with their descriptions.

2. **Pick or write the mapping.**
   - Synthea 3.3.0: use `omop_skill/mappings/synthea_3.3.0.yaml` as is.
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

4. **Validate every site.**
   `python omop_skill/scripts/validate_contract.py <output_dir>/<site>`
   If a trusted output for the same raw data exists, for example `synthea_cohorts/cohort_2/data/omop/<site>`, add `--reference <that_dir>`.

5. **Fix and repeat.** If validation fails, fix the mapping and rerun steps 3 and 4. Stop after three attempts and report what still fails.

6. **Report** per site: row counts per table, the validation result, and source codes from the profile that the mapping leaves out.

A person reviews every new mapping before its output replaces data that subproject 2 uses.
