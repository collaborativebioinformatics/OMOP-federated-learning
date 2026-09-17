# omop_skill: raw source to OMOP contract tables (MVP)

A Claude skill plus three scripts. Converts raw source CSVs into the four OMOP tables of `contract/data_contract.md`, one folder per site. That is the input subproject 2 reads.

| Step | Done by | File |
|---|---|---|
| Profile the source | DuckDB `SUMMARIZE` | `scripts/profile_source.py` |
| Write the mapping | Claude, following the skill | `mappings/<source>.yaml` |
| Run the mapping | DuckDB | `scripts/run_mapping.py` |
| Check the output | pandas, against `contract.yaml` | `scripts/validate_contract.py` |

The skill itself lives in `.claude/skills/omop-etl/SKILL.md`, so Claude Code picks it up when you open the repo.

## Run it on Synthea 3.3.0

```bash
pip install -r omop_skill/requirements.txt
python omop_skill/scripts/run_mapping.py --mapping omop_skill/mappings/synthea_3.3.0.yaml --source synthea_datasets/raw/site_a/csv --out omop_skill/output/site_a
python omop_skill/scripts/validate_contract.py omop_skill/output/site_a --reference synthea_datasets/omop/site_a
```

## Test

```bash
python -m unittest discover -s omop_skill/tests
```

The fixture in `tests/fixture_synthea/` uses the Synthea 3.3.0 column layout. `tests/expected_omop/` is what the hand-written mapping in `synthea_datasets/build.py` produces for it, so the test proves the skill's mapping gives the same tables.

## Limits

MVP. Concept IDs come from contract section 5, there is no Athena lookup yet. One mapping file per source.
