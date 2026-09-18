# Agent instructions

This repository turns raw health data into OMOP tables and trains models across sites on them.

- To convert, review, or check source data for OMOP, follow [`omop_skill/SKILL.md`](omop_skill/SKILL.md).
- For the UK Biobank vocabulary review route, also read [`omop_skill/review/AGENTS.md`](omop_skill/review/AGENTS.md).
- For federated learning on the OMOP tables, start at [`src/omopflare/README.md`](src/omopflare/README.md).

## Rules

- The repository is public. Never commit raw source data, credentials, or tokens. Generated data stays in the ignored `data/`, `output/`, and `work/` folders.
- A person approves every mapping. Your recommendation is never an approval.
- Don't edit `contract/data_contract.md`, `omop_skill/contract.yaml`, or the scripts to make a check pass. Fix the mapping, or report the data problem.

## Tests

```bash
python -m unittest discover -s omop_skill/tests
python -m unittest discover -s omop_skill/review/tests
```
