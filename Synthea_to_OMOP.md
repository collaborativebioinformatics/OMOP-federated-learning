# End-to-End Workflow: Synthea → OMOP CDM

This guide covers two connected stages:

- **Part A** — Install Synthea and generate a synthetic patient population (CSV export)
- **Part B** — Load that data into a PostgreSQL database and map it into the OMOP Common Data Model (CDM v5.4) using the OHDSI `ETL-Synthea` R package

Target stack: **Synthea (Java) → PostgreSQL → R / ETLSyntheaBuilder → OMOP CDM v5.4**

---

## Prerequisites

| Tool | Purpose | Notes |
|---|---|---|
| Java JDK 11+ | Run Synthea | `java -version` to check |
| Git | Clone repos | |
| PostgreSQL 12+ | Host OMOP CDM | Local or remote instance |
| R 4.x + RStudio (optional) | Run ETL package | |
| OHDSI Athena account | Download vocabulary | Free, sign up at https://athena.ohdsi.org |

---

## Part A — Generate Synthetic Data with Synthea

### A1. Clone and build

```bash
git clone https://github.com/synthetichealth/synthea.git
cd synthea
./gradlew build check test
```

### A2. Enable CSV export

Edit `src/main/resources/synthea.properties`:

```properties
exporter.csv.export = true
exporter.fhir.export = false
```

CSV is what the ETL tool expects — FHIR isn't needed for this path.

### A3. Generate a population

```bash
./run_synthea -p 1000 -s 12345 Massachusetts
```

- `-p 1000` → 1,000 patients
- `-s 12345` → fixed seed (reproducible)
- `Massachusetts` → state (optional city can follow)

Output lands in `output/csv/` — you should see files like `patients.csv`, `encounters.csv`, `conditions.csv`, `medications.csv`, `procedures.csv`, `observations.csv`, etc.

### A4. Sanity-check the output

```bash
wc -l output/csv/*.csv
head -5 output/csv/patients.csv
```

---

## Part B — ETL into OMOP CDM

### B1. Download the OMOP Vocabulary

1. Create a free account at https://athena.ohdsi.org
2. Select vocabularies you need (at minimum: SNOMED, RxNorm, LOINC, and the "OMOP genomic" defaults)
3. Download and unzip — you'll get files like `CONCEPT.csv`, `VOCABULARY.csv`, `CONCEPT_RELATIONSHIP.csv`, etc.

```bash
mkdir -p ~/omop/vocabulary
unzip vocabulary_download.zip -d ~/omop/vocabulary
```

### B2. Create the target PostgreSQL database

```bash
psql -U postgres -c "CREATE DATABASE synthea_omop;"
```

Create two schemas: one for raw Synthea data, one for OMOP CDM tables.

```sql
\c synthea_omop
CREATE SCHEMA native;
CREATE SCHEMA cdm_synthea;
```

### B3. Install the ETL-Synthea R package

```r
install.packages("devtools")
devtools::install_github("OHDSI/ETL-Synthea")
install.packages("DatabaseConnector")
```

(`DatabaseConnector` will also prompt to download the PostgreSQL JDBC driver the first time you connect — accept that.)

### B4. Run the ETL

```r
library(ETLSyntheaBuilder)
library(DatabaseConnector)

# --- connection details ---
cd <- DatabaseConnector::createConnectionDetails(
  dbms     = "postgresql",
  server   = "localhost/synthea_omop",
  user     = "postgres",
  password = "<your_password>",
  port     = 5432
)

synthea_schema <- "native"
cdm_schema     <- "cdm_synthea"
synthea_csv_dir <- "/path/to/synthea/output/csv"
vocab_dir       <- "/path/to/omop/vocabulary"

# --- 1. Clean slate (safe to skip on first run) ---
ETLSyntheaBuilder::DropVocabTables(cd, cdm_schema)
ETLSyntheaBuilder::DropEventTables(cd, cdm_schema)
ETLSyntheaBuilder::DropSyntheaTables(cd, synthea_schema)
ETLSyntheaBuilder::DropMapAndRollupTables(cd, cdm_schema)

# --- 2. Create table structures ---
ETLSyntheaBuilder::CreateVocabTables(cd, cdm_schema)
ETLSyntheaBuilder::CreateEventTables(cd, cdm_schema)
ETLSyntheaBuilder::CreateSyntheaTables(cd, synthea_schema)

# --- 3. Load raw data ---
ETLSyntheaBuilder::LoadSyntheaTables(cd, synthea_schema, synthea_csv_dir)
ETLSyntheaBuilder::LoadVocabFromCsv(cd, cdm_schema, vocab_dir)

# --- 4. Build mapping/rollup tables and run the transform ---
ETLSyntheaBuilder::CreateVocabMapTables(cd, cdm_schema)
ETLSyntheaBuilder::CreateVisitRollupTables(cd, cdm_schema, synthea_schema)
ETLSyntheaBuilder::LoadEventTables(cd, cdm_schema, synthea_schema)
```

This mirrors the OHDSI reference workflow: drop → create structure → load raw Synthea CSVs → load vocabulary → build concept-mapping/rollup tables → populate OMOP event tables from the mapped, rolled-up data.

### B5. Verify the load

```sql
SELECT COUNT(*) FROM cdm_synthea.person;
SELECT COUNT(*) FROM cdm_synthea.condition_occurrence;
SELECT COUNT(*) FROM cdm_synthea.drug_exposure;
SELECT COUNT(*) FROM cdm_synthea.visit_occurrence;
```

Row counts should roughly track your Synthea population size and each patient's encounter/condition history.

### B6. (Recommended) Run OHDSI Data Quality checks

Once loaded, run OHDSI's **Data Quality Dashboard (DQD)** or **ACHILLES** against `cdm_synthea` to check conformance, completeness, and plausibility — this catches mapping gaps before you build cohorts on top of the data.

```r
install.packages("remotes")
remotes::install_github("OHDSI/DataQualityDashboard")

library(DataQualityDashboard)
executeDqChecks(
  connectionDetails = cd,
  cdmDatabaseSchema  = "cdm_synthea",
  resultsDatabaseSchema = "cdm_synthea",
  cdmSourceName = "Synthea Synthetic Data",
  outputFolder = "~/omop/dqd_results"
)
```

---

## Putting it together (single shell script for Part A + kickoff)

```bash
#!/bin/bash
set -e

# --- Part A: Generate Synthea data ---
git clone https://github.com/synthetichealth/synthea.git
cd synthea
sed -i 's/exporter.csv.export = false/exporter.csv.export = true/' src/main/resources/synthea.properties
./gradlew build check test
./run_synthea -p 1000 -s 12345 Massachusetts

echo "Synthea CSVs ready at $(pwd)/output/csv"
echo "Now run the R script (Part B) pointing synthea_csv_dir at this path."
```

Then run the R script from **B4** to complete the OMOP load.

---

## Notes and gotchas

- **CDM version**: `ETLSyntheaBuilder` currently targets CDM v5.3/5.4 depending on the release — pin the package version if you need exact compatibility with a downstream tool like ATLAS.
- **Vocabulary size**: the full OHDSI vocabulary download is large (several GB uncompressed) and the `CONCEPT` table load is usually the slowest step — budget time for it.
- **Mapping gaps**: Synthea's synthetic conditions/procedures don't always map cleanly to standard concepts for highly granular domains (e.g., precise anatomical site). Expect to spot-check condition_occurrence and procedure_occurrence after loading.
- **Scale**: for populations beyond ~50–100k patients, the R-based ETL can get slow; at that scale, consider the newer SQL/Spark-based ETL pipelines from the OHDSI community instead of ETLSyntheaBuilder.
- **Reproducibility**: always pass `-s <seed>` to Synthea if you need to regenerate the same population later for comparison.
