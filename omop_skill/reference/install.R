# Install the R packages ETL-Synthea needs.
# Set OMOP_SKILL_R_LIB to install into a separate library folder, for example inside the project.
lib <- Sys.getenv("OMOP_SKILL_R_LIB")
if (nzchar(lib)) {
  dir.create(lib, showWarnings = FALSE, recursive = TRUE)
  .libPaths(c(lib, .libPaths()))
} else {
  lib <- .libPaths()[1]
}
options(repos = c(CRAN = "https://cloud.r-project.org"))

cran <- c("remotes", "DatabaseConnector", "SqlRender", "CommonDataModel", "duckdb", "readr")
missing <- setdiff(cran, rownames(installed.packages()))
if (length(missing) > 0) install.packages(missing, lib = lib)

if (!"ETLSyntheaBuilder" %in% rownames(installed.packages())) {
  remotes::install_github("OHDSI/ETL-Synthea", lib = lib, upgrade = "never")
}

for (p in c("ETLSyntheaBuilder", "DatabaseConnector", "SqlRender", "CommonDataModel", "duckdb")) {
  cat(sprintf("%-20s %s\n", p, as.character(packageVersion(p))))
}
