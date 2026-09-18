# omop_skill: raw source to OMOP contract tables (MVP)

A Claude skill plus three scripts. Converts raw source CSVs into the four OMOP tables of `contract/data_contract.md`, one folder per site. That is the input subproject 2 reads.

| Step | Done by | File |
|---|---|---|
| Profile the source | DuckDB `SUMMARIZE` | `scripts/profile_source.py` |
| Write the mapping | Claude, following the skill | `mappings/<source>.yaml` |
| Run the mapping | DuckDB | `scripts/run_mapping.py` |
| Check the output | pandas, against `contract.yaml` | `scripts/validate_contract.py` |
| Quality report | DuckDB and pandas: completeness, dates, mapping rate | `scripts/qc_report.py` |
| Split one run into sites | DuckDB | `scripts/split_sites.py` |
| Compare with a reference ETL | DuckDB, contract section 8 | `scripts/compare_reference.py` |

`contract.yaml` is the current data contract (2026-09-18, 22 concept IDs), used by `mappings/synthea_3.3.0_contract_rev3.yaml`. `contract_rev1.yaml` is revision 1, which `cohort_2` and `mappings/synthea_3.3.0.yaml` follow.

The skill itself lives in `.claude/skills/omop-etl/SKILL.md`, so Claude Code picks it up when you open the repo.

## Run it on cohort_2 (Synthea 3.3.0, five sites)

```bash
pip install -r omop_skill/requirements.txt
python omop_skill/scripts/run_mapping.py --mapping omop_skill/mappings/synthea_3.3.0.yaml --source synthea_cohorts/cohort_2/data/source_compact/site_a --out omop_skill/output/site_a
python omop_skill/scripts/validate_contract.py omop_skill/output/site_a --reference synthea_cohorts/cohort_2/data/omop/site_a
```

Gzipped source files work as they are. On all five sites of cohort_2 the output passes the contract check and is identical to `synthea_cohorts/cohort_2/data/omop/`, about 5 seconds per site.

## Test

```bash
python -m unittest discover -s omop_skill/tests
```

The fixture in `tests/fixture_synthea/` uses the Synthea 3.3.0 column layout. `tests/expected_omop/` is what the hand-written mapping in `synthea_datasets/build.py` produces for it, so the test proves the skill's mapping gives the same tables.

## Results, tests, limits

[RESULTS.md](RESULTS.md) has the numbers per site, how correctness was tested, how the skill differs from the hand-written ETL, and what it can't do yet.

Short version: on Synthea the output is identical to the hand-written ETL. The difference is how it gets there, a mapping file instead of code, a contract check on every output, and a profile of the source to write the next mapping from.
