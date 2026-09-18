# omop_skill: MVP results

State on 2026-09-17, branch `omop-skill-mvp`.

Two test series.
Revision 1 of `contract/data_contract.md` on `cohort_2`, against the team's hand-written ETL (first sections).
Revision 2 blind, on a fresh Synthea run, compared against OHDSI ETL-Synthea (section "Blind run").

Raw Synthea data in, the four contract tables out, checked against the contract.
On all five sites of `cohort_2` the output is identical to the tables the team wrote by hand, and `omopflare` from subproject 2 reads it without errors.

## Results

| Site | Persons | Measurements | T2DM diagnoses | Contract check | vs `cohort_2/data/omop` | omopflare |
|---|---|---|---|---|---|---|
| site_a | 1,235 | 21,489 | 14 | OK | identical | 0 errors |
| site_b | 1,000 | 27,100 | 63 | OK | identical | 0 errors |
| site_c | 1,128 | 65,928 | 196 | OK | identical | 0 errors |
| site_d | 507 | 18,412 | 65 | OK | identical | 0 errors |
| site_e | 744 | 16,573 | 52 | OK | identical | 0 errors |

About 5 seconds per site on a Windows laptop.
`observation_period` has one row per person at every site.

## How correctness was tested

1. **Unit test on a fixture.** Synthea 3.3.0 column layout, 4 patients, with a patient without encounters, rows for a patient missing from `patients.csv`, an unmapped code, a non-numeric value, a unit outside the map, an unknown gender, and a timestamp just before midnight UTC. The expected output comes from `synthea_datasets/build.py`, the hand-written mapping. The skill has to match it exactly.
2. **Real data.** Every site of `cohort_2`, output compared column by column with `synthea_cohorts/cohort_2/data/omop/`. Numbers within 1e-9, everything else exact.
3. **Contract check** on every output: column names and order, integers, decimals, `YYYY-MM-DD` dates, only concept IDs from the contract, unique IDs, every `person_id` present in `person`.
4. **Downstream.** `omopflare.validate`, `extract` and `to_matrix` from branch `omopflare-package` on all five outputs. 0 errors. Two warnings, because the contract tables ship without VOCABULARY and CONCEPT tables.

What the tests don't prove:

- The reference is the team's own hand-written ETL, not OHDSI ETL-Synthea. A logic error there is reproduced here.
- The compact source was filtered by the same team code. The full 3.9 GB export has not been run.

## Blind run: fresh Synthea data, contract revision 2

Raw data first, OMOP later.
The mapping was written after the data existed, from the contract text and the profile only, without any existing OMOP output to copy from.

| Step | Result |
|---|---|
| Synthea 3.3.0 | 1,162 patients (1,000 alive), Massachusetts, 2.1 GB |
| Mapping | `mappings/synthea_3.3.0_contract_rev2.yaml` |
| Conversion | 8 s: 1,162 persons, 1,162 observation periods, 151,219 measurements, 94 T2DM diagnoses |
| Structure checks | Pass: columns, types, concept IDs, person links |
| Plausibility check | Fails: 3,103 HbA1c values below 3 % in 87 persons, 99 systolic values below 60 in 9 adults |
| Site split | site_a 387, site_b 388, site_c 387 persons |
| Comparison with ETL-Synthea | Measurements and diagnoses identical, persons identical except race for 11 patients, see below |

The raw data comes from:

```bash
java -jar synthea-3.3.0.jar -s 20260917 -cs 20260917 -r 20260917 -e 20260917 -p 1000 --exporter.csv.export=true --exporter.fhir.export=false --exporter.years_of_history=0 Massachusetts
```

Findings:

- Synthea's HbA1c runs low: median 3.9 %, 5th percentile 2.8 %, also for patients with type 2 diabetes. The plausibility failure comes from the data, the ETL copies the values. HbA1c as the target needs a second look.
- Rerunning the command gives the same rows in a different order. `person_id` follows file order, so the `person_id % 3` split moves patients between sites across reruns. Numbering patients sorted by `Id` would fix that.
- The contract doesn't say which remainder goes to which site. `split_sites.py` uses 0 for site_a.

### Comparison with OHDSI ETL-Synthea

ETL-Synthea converted the same raw data into a full OMOP CDM 5.4 database on DuckDB: 1,162 persons, 199,567 visits, 50,649 conditions, 1,952,281 measurements, 148,431 drug exposures.
`scripts/compare_reference.py` compared it with the skill output as section 8 of the contract describes, persons joined on `person_source_value`.
The comparison script was self-tested first: an identical copy passes, four planted changes are all found.

| Table | Result |
|---|---|
| person | 1,162 in both. Gender, year of birth and ethnicity identical. Race differs for 11 persons. |
| measurement | Identical: 151,219 rows, every value on every date |
| condition_occurrence | Identical: 94 persons with type 2 diabetes, every start date |

The 11 race differences are the 11 patients with Synthea race `hawaiian`.
The contract maps it to 8557, Native Hawaiian or Other Pacific Islander. ETL-Synthea maps only white, black and asian and writes 0 for the rest.
The difference comes from the contract, not from a mapping error.

The vocabulary in the same database confirms all 16 concept IDs of contract section 6: each exists, is standard and valid, and the four source codes map to them via `Maps to`.
Athena release `v5.0 29-AUG-26`, the date section 2 of the contract asks for.
`native` (5 patients) could map to 8657, American Indian or Alaska Native, instead of 0.

Rebuilding the reference: [reference/README.md](reference/README.md).

### Current contract (2026-09-18): 12 source codes

The contract grew to 3 condition codes and 9 measurement codes.
Same raw run, same ETL-Synthea database, reference re-exported for the 12 codes, mapping `mappings/synthea_3.3.0_contract_rev3.yaml`.
All 22 concept IDs of the contract checked against Athena v5.0 29-AUG-26: valid, standard, and the `Maps to` target of their source code.
The Synthea units match the units the contract lists.

| Table | Skill | ETL-Synthea | Result |
|---|---|---|---|
| person | 1,162 | 1,162 | Identical except race for the 11 `hawaiian` patients, as before |
| measurement | 254,891 | 254,891 | Same rows on every date, 9 values differ |
| condition_occurrence | 120 rows, 113 persons | 113 persons | Identical, every start date |

Contract check: OK. Conversion: 8 s.

The 9 value differences are LDL cholesterol (LOINC 18262-6) below zero, as low as -28.1 mg/dL.
Synthea writes these values. The skill copies them as the contract says, ETL-Synthea leaves `value_as_number` empty.
A negative LDL is impossible, so the fix belongs in QC: a plausibility check on lab values catches it.

## How it differs from the hand-written ETL

| | Hand-written ETL (`build_datasets.py`) | omop_skill |
|---|---|---|
| Output on Synthea | The contract tables | The same tables, identical |
| Where the mapping lives | Python code | YAML file with SQL expressions |
| New data source | New script | New YAML file, the engine stays |
| Who writes the mapping | A developer | Claude from the profile, reviewed by a person |
| Engine | pandas, whole files in memory | DuckDB SQL, reads `.csv` and `.csv.gz` |
| Output check | Row counts and sha256 in a manifest | Contract check, optional comparison with a reference |
| Source profiling | None | DuckDB report per source folder |
| Generates Synthea data | Yes, downloads and runs Synthea | No, starts from raw CSV files |
| Tests | None | Fixture with edge cases |

For one known source the hand-written script is simpler and enough.
The skill pays off from the second source on, and as a check anyone can run on any output folder.

## What it can do

- Turn Synthea 3.3.0 CSVs into the four contract tables, reproducibly, locally, without a database server.
- Take a new source with a new YAML file and no code change.
- Check any output folder against the contract, including output from someone else.
- Profile raw data so a mapping starts from facts.

## What it can't do yet

- Prove it works on a source other than Synthea. The revision 2 mapping was written blind from contract and profile, but the source was Synthea again.
- Look up concepts on its own. The concept IDs come from the contract. They were checked against the Athena vocabulary once, not by the skill.
- Cover more than 4 tables and 3 source codes: BMI, systolic blood pressure, type 2 diabetes. Race and ethnicity are always 0.
- Produce a full OMOP database: no vocabulary tables, visits, drugs or procedures.

## Limitations

- Loads all source rows into memory, single-threaded, to keep row order. Speed and memory on the full export are unknown.
- `contract.yaml` duplicates `contract/data_contract.md`. Change both together.
- Mapping files contain SQL. Run only mappings a person has reviewed.
- Tested on Windows only, not on macOS.
- Writes CSV. `plan.md` mentions Parquet.

## Application

- **Now:** build the subproject 2 input from any Synthea site, and rebuild it when the raw data changes.
- **Quality gate:** every site checks its own output against the contract before training. No data leaves the site, which fits the federated setup.
- **Proof of concept for "any source to OMOP":** point the skill at a second source, let Claude write the mapping, compare with a reference. That is the missing step for the claim of subproject 1.
