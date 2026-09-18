# Concordia

**The bottleneck was never the learning.** Automated biobank-to-OMOP harmonisation for federated analysis.

*Project 5 at the Nordic Biobank × NVIDIA Federated Learning Hackathon, Copenhagen 2026*

**ETL pipeline and federated learning framework for OMOP Common Data Model data.**

Convert a biobank dataset to OMOP, validate it, and run federated learning across sites — 
all in one reproducible pipeline.

Federated learning has worked for years, and it still almost never happens: every biobank stores its data differently, and a hand-built ETL per source takes months.
We automated that step. Raw biobank data in, harmonised OMOP out, quality controlled, trained across sites. Nothing leaves the institution.
Collaboration stops being a technical problem. It becomes a choice.

## How it works

![Concordia: from raw biobank data to a federated model](presentation/concordia_workflow.jpg)

At every site, an AI agent writes the mapping from a profile of the raw data, a domain expert approves it, a QC gate checks the four OMOP tables, and the dataloader streams them to the GPU.
NVFlare trains across the sites. Only model weights cross a boundary.
Instructions for any coding agent: [`omop_skill/SKILL.md`](omop_skill/SKILL.md).

## Results

| | |
| --- | --- |
| Mapping | Written blind by an agent. Against OHDSI ETL-Synthea on 1,162 patients: 254,882 of 254,891 measurement values identical, no difference in diagnoses. 8 s for 2.1 GB, no server. |
| Quality control | Caught what both conversions let through, negative LDL and an HbA1c median of 3.9 %, and shows before training whether the sites differ enough to be worth federating. |
| UK Biobank | The same skill maps the raw extract. All eight assessment centres pass the contract. |
| Scale | 1 million patients and 3.39 billion rows: 7.9 s scan, 6.2 GB of memory. |

![Fragmenting a cohort costs a local model 10 AUROC points; federating recovers them](presentation/figures/federation_benchmark.png)

One real cohort of 964 patients, split across 1 to 16 sites. Alone, a site falls from 0.87 to 0.77 AUROC. Federated, it holds at 0.87 to 0.92, level with pooled.
On five Synthea sites, federated matches pooled (AUROC 0.582 against 0.586). On synthetic UK Biobank, which has no signal, no model beats chance, so the negative control holds.

Details: [`omop_skill/RESULTS.md`](omop_skill/RESULTS.md), [`examples/synthea_diabetes`](examples/synthea_diabetes/README.md), [`examples/ukb_diabetes`](examples/ukb_diabetes/README.md), and the [final slides](presentation/concordia_final_slides.pdf).

## Quickstart

Subproject 1, raw Synthea data of one site to OMOP, then checked against the contract:

```bash
pip install -r omop_skill/requirements.txt
python omop_skill/scripts/run_mapping.py --mapping omop_skill/mappings/synthea_3.3.0.yaml --source synthea_cohorts/cohort_2/data/source_compact/site_a --out omop_skill/output/site_a
python omop_skill/scripts/validate_contract.py omop_skill/output/site_a --contract omop_skill/contract_rev1.yaml
```

Subproject 2, federated training over the five sites of `cohort_2`:

```bash
pip install -e .
python examples/omop_t2dm/run.py --sites synthea_cohorts/cohort_2/data/omop
```

A full NVFlare job is in [`src/omopflare/README.md`](src/omopflare/README.md).

## Repository

| Path | What |
| --- | --- |
| [`omop_skill/`](omop_skill/README.md) | Subproject 1: raw data to OMOP, and the checks |
| [`src/omopflare/`](src/omopflare/README.md) | Subproject 2: OMOP dataloader and federated learning with NVFlare |
| [`examples/`](examples/) | End-to-end runs (`ukb_diabetes`, `synthea_diabetes`) and the benchmarks |
| [`contract/`](contract/data_contract.md) | The data contract both subprojects hand over on |
| `synthea_cohorts/` | Synthetic cohorts |
| [`presentation/`](presentation/), [`docs/`](docs/index.html) | Slides and project page |

## Limits

Proof of concept: synthetic data, simulated sites, one real cohort for the benchmark.
An AI drafts every mapping, a person approves it. Next: HUNT Cloud over AWS.

## Members

charles, solvi, lukas, maria, max, nik, mia, kev
