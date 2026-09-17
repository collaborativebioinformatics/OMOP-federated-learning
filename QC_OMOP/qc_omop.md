## QC steps for OMOP data

1. missingness (missing values)
2. all tables present
3. check the format and logic of the date variables (ensure index date before follow-up and end dates)
4. check for fraction of successful OMOP conversions
5. compare summary statistics of variables
6. ensure correct units

These sanity checks should ideally be comparisons between raw data, our OMOP conversions, and the Synthea OMOP conversion.

## Tools to do QC/sanity checks in OMOP data: <br>
### Achilles <br>
R package: OHDSI/Achilles <br>
Generates descriptive statistics across the whole CDM (counts, distributions by year/age/gender, etc.) <br>
Powers visual exploration in ATLAS <br>

### [Consolidated tool](https://ohdsi.github.io/DataQualityDashboard/) for visual checks and QC of OMOP data.

### Check	Primary Tool	Secondary/Support
Missingness	- DQD (Completeness) -	White Rabbit (source comparison) <br>
All tables present -	Achilles (row counts)	- Manual DDL check <br>
Date format/logic -	DQD (Plausibility)	- Custom SQL <br>
Conversion/mapping rate	- Achilles Heel	- Usagi, custom SQL <br>


## Final OMOP Data QC Checklist

A practical QC pass for a newly converted OMOP CDM dataset, organized around four core checks, with tools and concrete steps for each.

### 1. Missingness (Missing Values)

**Goal:** Identify fields with unexpected or excessive nulls that could indicate ETL failures.

**Steps:**
- Run **Data Quality Dashboard (DQD)** — it includes a full set of *Completeness* checks that flag high null rates on required or clinically important fields (e.g., `condition_concept_id`, `measurement_date`).
- Spot-check NOT NULL fields per the OMOP CDM spec (e.g., `person_id`, `visit_occurrence_id` where required) with simple SQL:
  ```sql
  SELECT COUNT(*) AS null_count
  FROM condition_occurrence
  WHERE condition_concept_id IS NULL;
  ```
- Compare missingness rates against source data (via **White Rabbit** profiling) — a jump in nulls post-ETL usually means a mapping or transformation bug, not a true data gap.
- Set thresholds (e.g., flag if >5% missing on key fields) and track over time.

**Tools:** DQD (primary), White Rabbit (source comparison)


### 2. All Tables Present

**Goal:** Confirm the CDM instance has the full expected table set and none are empty/dropped during ETL.

**Steps:**
- Check the schema against the OMOP CDM DDL for your target version (5.3 / 5.4) — confirm every required table exists.
- Run row counts per table; flag any core clinical table (person, visit_occurrence, condition_occurrence, drug_exposure, etc.) that's unexpectedly empty.
  ```sql
  SELECT table_name, COUNT(*) 
  FROM information_schema.tables ...
  -- or loop row counts per table
  ```
- **Achilles** is useful here too — it generates descriptive stats across the *entire* CDM, so a missing/empty table shows up immediately as zero counts in its output.

**Tools:** Achilles, manual schema/DDL check


### 3. Date Format Checks

**Goal:** Ensure date/datetime fields are valid, correctly typed, and internally consistent.

**Steps:**
- Validate all date fields parse correctly as dates (not stored as strings, not malformed).
- Check for logical/temporal plausibility (this overlaps with DQD's *Plausibility* checks):
  - `birth_datetime` not in the future
  - `death_date` not before other clinical events
  - `*_start_date` not after `*_end_date`
  - Event dates fall within the person's `observation_period`
- DQD runs many of these automatically under Plausibility — no need to write all the SQL by hand.

**Tools:** DQD (Plausibility checks), targeted SQL for date logic


### 4. Fraction of Successful OMOP Conversions (Mapping Rate)

**Goal:** Measure how much source data was successfully mapped to standard OMOP concepts vs. left unmapped.

**Steps:**
- Calculate the % of records with `concept_id = 0` (unmapped) per domain table — high rates signal vocabulary/mapping gaps.
  ```sql
  SELECT 
    SUM(CASE WHEN condition_concept_id = 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS pct_unmapped
  FROM condition_occurrence;
  ```
- Review mapping decisions in **Usagi** for any source codes marked low-confidence or manually reviewed.
- Use **Achilles Heel** — it explicitly flags high proportions of unmapped (`concept_id = 0`) records as a data quality warning.
- Track mapping rate by table/domain over time; a sudden drop after an ETL update usually means a vocabulary version mismatch or broken mapping step.

**Tools:** Achilles / Achilles Heel, Usagi, custom SQL


## Suggested Order of Operations

1. Confirm schema + all tables present (manual check + Achilles row counts)
2. Run **Achilles + Achilles Heel** for descriptive stats and flagged warnings
3. Run **DQD** for the full Conformance / Completeness / Plausibility report (covers missingness + date logic)
4. Pull mapping rate stats (concept_id = 0) per table
5. Document accepted/known issues — not everything flagged needs to be "fixed"; some reflect genuine source data limitations


