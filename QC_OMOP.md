## QC steps for OMOP data

1. missingness (missing values)
2. all tables present
3. check the format and logic of the date variables (ensure index date before end dates)
4. check for fraction of successful OMOP conversions
5. compare summary statistics of variables

These sanity checks should ideally be comparisons between raw data, our OMOP conversions, and the Synthea OMOP conversion.

## Can do these sanity checks with tools like: <br>
### Achilles <br>
R package: OHDSI/Achilles <br>
Generates descriptive statistics across the whole CDM (counts, distributions by year/age/gender, etc.) <br>
Powers visual exploration in ATLAS <br>

### Can include https://ohdsi.github.io/DataQualityDashboard/ as a tool for visual checks.

### Check	Primary Tool	Secondary/Support
Missingness	- DQD (Completeness) -	White Rabbit (source comparison) <br>
All tables present -	Achilles (row counts)	- Manual DDL check <br>
Date format/logic -	DQD (Plausibility)	- Custom SQL <br>
Conversion/mapping rate	- Achilles Heel	- Usagi, custom SQL <br>

