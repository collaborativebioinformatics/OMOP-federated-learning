# Run OHDSI ETL-Synthea on DuckDB, in two phases around a DuckDB-native CSV load.
#
# DatabaseConnector::insertTable on DuckDB writes a table literally named "native.patients" into schema main
# instead of filling native.patients, so ETL-Synthea's own loaders leave the Synthea and vocabulary tables empty
# without an error. load_csv_duckdb.py loads the CSV files instead. Everything that maps and transforms is
# still ETL-Synthea's SQL.
#
# Usage:
#   Rscript run_etl_synthea.R create    <output_dir>
#   python  load_csv_duckdb.py <output_dir>/etl_synthea.duckdb --synthea <csv_dir> --vocabulary <athena_dir>
#   Rscript run_etl_synthea.R transform <output_dir>
#
# Set OMOP_SKILL_R_LIB if the packages from install.R live in a separate library folder.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2 || !args[1] %in% c("create", "transform")) {
  stop("usage: Rscript run_etl_synthea.R create|transform <output_dir>")
}
phase <- args[1]
dir.create(file.path(args[2], "all"), recursive = TRUE, showWarnings = FALSE)
outDir <- normalizePath(args[2], winslash = "/", mustWork = TRUE)

lib <- Sys.getenv("OMOP_SKILL_R_LIB")
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))
suppressPackageStartupMessages({
  library(DatabaseConnector)
  library(ETLSyntheaBuilder)
})

step <- function(label, expr) {
  started <- Sys.time()
  cat(format(started, "%H:%M:%S"), label, "\n")
  force(expr)
  cat(sprintf("   %.0f s\n", as.numeric(difftime(Sys.time(), started, units = "secs"))))
}

dbFile <- file.path(outDir, "etl_synthea.duckdb")
cd <- createConnectionDetails(dbms = "duckdb", server = dbFile)
cdmSchema <- "cdm"
syntheaSchema <- "native"
cdmVersion <- "5.4"
syntheaVersion <- "3.3.0"

if (phase == "create") {
  if (file.exists(dbFile)) stop("database already exists, remove it first: ", dbFile)
  conn <- connect(cd)
  executeSql(conn, "CREATE SCHEMA cdm; CREATE SCHEMA native;")
  disconnect(conn)
  step("CreateCDMTables", CreateCDMTables(connectionDetails = cd, cdmSchema = cdmSchema, cdmVersion = cdmVersion))
  step("CreateSyntheaTables", CreateSyntheaTables(connectionDetails = cd, syntheaSchema = syntheaSchema, syntheaVersion = syntheaVersion))
  cat("next: python load_csv_duckdb.py", dbFile, "--synthea <csv_dir> --vocabulary <athena_dir>\n")
}

if (phase == "transform") {
  step("CreateMapAndRollupTables", CreateMapAndRollupTables(connectionDetails = cd, cdmSchema = cdmSchema, syntheaSchema = syntheaSchema, cdmVersion = cdmVersion, syntheaVersion = syntheaVersion))
  step("LoadEventTables", LoadEventTables(connectionDetails = cd, cdmSchema = cdmSchema, syntheaSchema = syntheaSchema, cdmVersion = cdmVersion, syntheaVersion = syntheaVersion))

  conn <- connect(cd)
  export <- function(name, sql) {
    df <- querySql(conn, sql)
    names(df) <- tolower(names(df))
    readr::write_csv(df, file.path(outDir, "all", paste0(name, ".csv")), na = "")
    cat(sprintf("exported %-22s %9d rows\n", name, nrow(df)))
  }
  # The tables and concepts section 8 of the data contract compares.
  export("person", "SELECT person_id, gender_concept_id, year_of_birth, race_concept_id, ethnicity_concept_id, person_source_value FROM cdm.person ORDER BY person_id")
  export("measurement", "SELECT person_id, measurement_concept_id, measurement_date, value_as_number, unit_concept_id FROM cdm.measurement WHERE measurement_concept_id IN (3004410, 3038553, 3004249) ORDER BY person_id, measurement_date")
  export("condition_occurrence", "SELECT person_id, condition_concept_id, condition_start_date FROM cdm.condition_occurrence WHERE condition_concept_id = 201826 ORDER BY person_id")
  print(querySql(conn, paste(
    "SELECT 'person' AS cdm_table, COUNT(*) AS n FROM cdm.person",
    "UNION ALL SELECT 'observation_period', COUNT(*) FROM cdm.observation_period",
    "UNION ALL SELECT 'visit_occurrence', COUNT(*) FROM cdm.visit_occurrence",
    "UNION ALL SELECT 'condition_occurrence', COUNT(*) FROM cdm.condition_occurrence",
    "UNION ALL SELECT 'measurement', COUNT(*) FROM cdm.measurement",
    "UNION ALL SELECT 'drug_exposure', COUNT(*) FROM cdm.drug_exposure"
  )))
  disconnect(conn)
}
