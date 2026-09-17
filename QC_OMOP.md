## QC steps for OMOP data

1. missingness (missing values)
2. all tables present
3. check the format of the date
4. check for fraction of successful OMOP conversions

Can do these sanity checks with tools like:
Achilles
R package: OHDSI/Achilles
Generates descriptive statistics across the whole CDM (counts, distributions by year/age/gender, etc.)
Powers visual exploration in ATLAS
