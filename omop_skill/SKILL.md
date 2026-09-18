---
name: omop-etl
description: Convert a raw health data source (CSV or TSV files, one folder per site) into the OMOP tables of contract/data_contract.md (person, observation_period, measurement, condition_occurrence) and quality-check them. Route A runs a YAML mapping with DuckDB when the source codes are already in the contract, such as Synthea. Route B proposes Athena vocabulary targets for a person to approve when codes need mapping, such as UK Biobank ICD-10. Use when someone asks to profile, map, convert, review, or validate source data for OMOP, or to prepare data for subproject 2.
---

# OMOP ETL skill

Raw source data in, the contract's OMOP tables out, checked. Subproject 2 (federated learning) reads the output folders.

Any agent that reads Markdown and runs shell commands can follow this file, and a person can run every step without an agent.
Every step is one command that writes files.
Your part is what no tool automates: writing a mapping or preparing a review. A person approves. You never do.

## Reference

- [OMOP Common Data Model documentation (OHDSI)](https://ohdsi.github.io/CommonDataModel/)
- [OMOP CDM v5.4 tables and fields](https://ohdsi.github.io/CommonDataModel/cdm54.html), the version the data contract uses
- [Athena](https://athena.ohdsi.org/), the OMOP standardized vocabularies
- [The Book of OHDSI, ETL chapter](https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html)

When `omop_skill/contract.yaml` doesn't answer a mapping question, look up the CDM v5.4 field definitions instead of relying on memory.

## Pick the route

| Source | Route |
|---|---|
| Source codes already listed in `omop_skill/contract.yaml`, for example Synthea (SNOMED, LOINC) | A: YAML mapping |
| Source codes that need a vocabulary mapping, for example UK Biobank ICD-10 | B: vocabulary review |

## Route A: YAML mapping

1. **Profile the source.**
   `python omop_skill/scripts/profile_source.py --source <site_dir> --out omop_skill/work/profile_<site>.md`
   Read tables, columns, empty values, and the most frequent codes with their descriptions.

2. **Pick or write the mapping.**
   - Synthea 3.3.0, current contract: `omop_skill/mappings/synthea_3.3.0_contract_rev3.yaml`
   - Synthea 3.3.0, revision 1 data such as `cohort_2`: `omop_skill/mappings/synthea_3.3.0.yaml`
   - UK Biobank wide extract: `omop_skill/mappings/ukb_pilot.yaml`
   - Anything else: copy the closest mapping to `omop_skill/mappings/<source>.yaml` and rewrite `files`, `concept_maps`, and every table from the profile.

   Rules:
   - Output columns, their order, and their types come from `contract.yaml`. Don't add, drop, or rename columns.
   - Use only concept IDs listed in `contract.yaml`. If a source code has no confirmed concept ID, leave it out and name it in the report.
   - Expressions are DuckDB SQL. Source tables are named after the keys under `files`, and each has a `src_row` column with its row number.
   - Build `person` first. Later tables join it as `omop_person` to look up `person_id`.
   - Helpers: `omop_concept('<map>', value)`, `omop_codes('<map>')`, `omop_date(value)` (UTC, `YYYY-MM-DD`).
   - Number IDs with `row_number() OVER (ORDER BY ...)` so the output is reproducible.

3. **Run it for every site.**
   `python omop_skill/scripts/run_mapping.py --mapping <mapping.yaml> --source <site_dir> --out <output_dir>/<site>`

## Route B: vocabulary review

Code in `omop_skill/review/`, details in `omop_skill/review/README.md` and `omop_skill/review/AGENTS.md`.

1. **Prepare the source extract.** UK Biobank: `omop_skill/review/ukb/README.md`. Report the participant count and the output path.
2. **Inspect.** Proposes targets, approves nothing.
   `python omop_skill/review/general_agent.py inspect --input <extract.tsv> --spec omop_skill/review/specs/<spec>.json --vocabulary <athena_dir> [--diagnosis-prefix E11] --output <run_dir>`
   This writes `<run_dir>/mapping_review.csv`: one row per source code, with the valid standard Athena targets.
3. **A person approves.** Show each candidate with concept ID, name, domain, and the vocabulary relationship. Record an approval in `mapping_review.csv` (`decision=approved`, `approved_target_ids`, `mapping_kind`, `evidence`) only when the person states it explicitly for that code and target. Your own recommendation is never an approval.
4. **Apply and replay the QC** with the same selection flags as in step 2:
   `python omop_skill/review/general_agent.py apply ... --review <run_dir>/mapping_review.csv --output <run_dir>/omop`
   `python omop_skill/review/general_agent.py qc ... --review <run_dir>/mapping_review.csv --output <run_dir>/omop`
5. **Plot coverage.**
   `python omop_skill/review/plot_qc.py --run <run_dir> --omop <run_dir>/omop --review <run_dir>/mapping_review.csv`
   `python omop_skill/review/mapping_inventory.py --run <run_dir> --omop <run_dir>/omop`

   Rules:
   - Identify the source coding system before proposing a target. UK Biobank field 41270 is ICD-10, not ICD10CM.
   - Map only to valid standard concepts through a documented vocabulary relationship or an explicitly reviewed local mapping. Don't guess from text similarity.
   - Keep unmapped source codes with concept 0 and their source value. Report coverage by records and by distinct codes.

## Check (both routes)

1. **Contract.**
   `python omop_skill/scripts/validate_contract.py <output_dir>/<site>`
   - Route B output carries extra standard OMOP columns and tables: add `--allow-extra`.
   - Revision 1 data such as `cohort_2`: add `--contract omop_skill/contract_rev1.yaml`.
2. **Quality.** Completeness, date logic, mapping rate.
   `python omop_skill/scripts/qc_report.py --output <output_dir>/<site> --out <output_dir>/<site>/qc_report.md --json <output_dir>/<site>/qc.json`
3. **Reference**, when a trusted conversion of the same raw data exists.
   `python omop_skill/scripts/compare_reference.py <output_dir>/all <reference_dir>/all`
   Persons are joined on `person_source_value`, so the IDs may differ. OHDSI ETL-Synthea as the reference: `omop_skill/reference/README.md`.
4. **Split one run into sites**, when the contract asks for it.
   `python omop_skill/scripts/split_sites.py <output_dir>/all <output_dir>`

## Fix, repeat, report

- If a check fails because of the mapping, fix the mapping and rerun. Stop after three attempts and report what still fails.
- If it fails because of the data, for example implausible values in the source, report it instead of bending the mapping.
- Don't edit the contract, the scripts, or the checks to make a check pass.
- Report per site: row counts per table, the check and QC results, and source codes the mapping leaves out.

A person approves every mapping, the YAML file in route A and `mapping_review.csv` in route B, before its output replaces data that subproject 2 uses.
