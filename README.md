# Project 5: Everything OMOP

**ETL pipeline and federated learning framework for OMOP Common Data Model data.**

Convert a biobank dataset to OMOP, validate it, and run federated learning across sites — 
all in one reproducible pipeline.

```mermaid
%%{init: {
  "flowchart": {
    "subGraphTitleMargin": {
      "top": 10,
      "bottom": 25
    }
  }
}}%%

flowchart TB

subgraph SP1["Subproject 1:<br/>Any biobank to OMOP"]
    direction LR
    A["Define minimal common<br/>tables and fields"]
    B["Collect ~2 datasets with<br/>existing OMOP conversions"]
    C["Build biobank schema to<br/>OMOP CDM POC"]
    D{"Reproduces published<br/>conversion?"}
    E["Convert ~3 datasets<br/>not yet in OMOP"]

    A --> B --> C --> D
    D -->|No: iterate| C
    D -->|Yes: primary eval passed| E
end

subgraph SP2["Subproject 2:<br/>Federated learning on OMOP"]
    direction LR
    F["Learn NVFlare"]
    G["Explore existing<br/>OMOP datasets"]
    H["Define site split and<br/>FedAvg baseline"]
    I["Build custom OMOP dataloader<br/>for NVFlare"]
    K["Train simple linear model<br/>across simulated sites"]

    F --> G --> H --> I --> K
end

E -->|OMOP datasets| J["Joint pipeline:<br/>Raw biobank → OMOP →<br/>Federated training"]
K -->|Dataloader and model| J

classDef blue fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#111827
classDef green fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#111827
classDef yellow fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#111827
classDef purple fill:#ede9fe,stroke:#7c3aed,stroke-width:3px,color:#111827

class A,B,C,E blue
class F,G,H,I,K green
class D yellow
class J purple
```

**Subproject 1** gets any biobank dataset into OMOP.
**Subproject 2** runs federated learning over the result.
The diagram above is the plan; `plan.md` has the schedule.

A one-page summary of the whole project is in [`docs/index.html`](docs/index.html); open it in a browser.

## Layout

| path | what |
| --- | --- |
| `src/omopflare` | the library: OMOP feature extraction for federated learning ([README](src/omopflare/README.md)) |
| `examples/omop_t2dm` | federated T2DM risk over any cohort's sites |
| `examples/` | NVFlare hello-world samples and earlier demos |
| `synthea_cohorts` | the cohorts, one folder per generation method |
| `contract` | the OMOP table contract the two subprojects hand over on |

## Quickstart

```bash
pip install -e .
python examples/omop_t2dm/run.py --sites synthea_cohorts/cohort_2/data/omop
```

```python
import omopflare as of

spec = of.FeatureSpec(
    features=(of.Feature("bmi", 3038553, "measurement", unit_concept_id=9531, plausible_range=(10.0, 80.0)),),
    vocabulary_version="v5.0 31-AUG-24",
    lookback_days=365,
)
site = of.OmopSource("/data/omop/site_a")
of.validate(site, spec, strict=True)
person_ids, X = of.feature_matrix(site, spec, "select person_id, current_date as index_date from person")
```

A full NVFlare job is in [src/omopflare/README.md](src/omopflare/README.md).

## Members

charles, solvi, lukas, maria, max, nik, mia, kev
