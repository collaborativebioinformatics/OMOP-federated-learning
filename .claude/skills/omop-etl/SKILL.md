---
name: omop-etl
description: Convert a raw health data source (CSV or TSV files, one folder per site) into the OMOP tables of contract/data_contract.md (person, observation_period, measurement, condition_occurrence) and quality-check them. Route A runs a YAML mapping with DuckDB when the source codes are already in the contract, such as Synthea. Route B proposes Athena vocabulary targets for a person to approve when codes need mapping, such as UK Biobank ICD-10. Use when someone asks to profile, map, convert, review, or validate source data for OMOP, or to prepare data for subproject 2.
---

The instructions for this skill live in `omop_skill/SKILL.md`, so every agent reads the same file. Read it and follow it.
