# omop_skill: MVP results

State on 2026-09-17, branch `omop-skill-mvp`.

Checked against revision 1 of `contract/data_contract.md`: observation period from encounters, BMI and systolic blood pressure.
Revision 2 changed the contract after these tests: HbA1c as the outcome, race and ethnicity concepts, observation period from observation and condition dates, plausibility ranges.
The skill does not support revision 2 yet.

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

- Generate a mapping on its own. The Synthea mapping was written to match `build.py`, not derived from the profile alone. Claude has not written a mapping for a new source yet.
- Look up concepts. Only the 9 concept IDs of the contract are known, nothing is checked against Athena.
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
