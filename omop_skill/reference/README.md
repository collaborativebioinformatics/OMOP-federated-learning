# ETL-Synthea reference on DuckDB

Builds the reference OMOP tables with OHDSI ETL-Synthea, without a database server, for `scripts/compare_reference.py`.

Needs R 4.x, Java, Python with duckdb, and an [Athena](https://athena.ohdsi.org/) vocabulary download with at least SNOMED, LOINC, RxNorm, UCUM, Gender, Race and Ethnicity.

```bash
Rscript omop_skill/reference/install.R
Rscript omop_skill/reference/run_etl_synthea.R create runs/run_01/reference
python omop_skill/reference/load_csv_duckdb.py runs/run_01/reference/etl_synthea.duckdb --synthea runs/run_01/raw/csv --vocabulary runs/vocabulary
Rscript omop_skill/reference/run_etl_synthea.R transform runs/run_01/reference
python omop_skill/scripts/compare_reference.py runs/run_01/omop/all runs/run_01/reference/all
```

The transform exports `person`, `measurement` and `condition_occurrence` to `<output_dir>/all/`, filtered to the concepts of the data contract.

## Why a Python loader

On DuckDB, `ETLSyntheaBuilder::LoadSyntheaTables` and `LoadVocabFromCsv` write tables literally named `native.patients` into schema `main` and leave the real tables empty, without an error.
The whole CDM then ends up empty.
`load_csv_duckdb.py` fills the tables ETL-Synthea created instead. Mapping and transformation stay ETL-Synthea's SQL.

## Runtime

Windows laptop, 1,162 patients (2.1 GB raw), 4.6 GB vocabulary: loading about 2 minutes, transform about 2 minutes.
Tested with ETLSyntheaBuilder 2.1, DatabaseConnector 7.2.0, duckdb 1.5.5, R 4.6.1.
