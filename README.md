# Project 5: Everything OMOP

```mermaid
flowchart TB
    subgraph SP1["Subproject 1: Any biobank to OMOP"]
        A["Agree on minimal required tables<br/>and fields common to biobanks"]
        B["Collect ~2 datasets with existing<br/>OMOP conversions, e.g. UK Biobank"]
        C["Skill / framework POC<br/>biobank schema to OMOP CDM"]
        D{"Reproduces the published<br/>conversion?"}
        E["Convert ~3 datasets<br/>not yet in OMOP"]
        A --> B --> C --> D
        D -->|no| C
        D -->|yes, primary eval passed| E
    end

    subgraph SP2["Subproject 2: Federated learning on OMOP"]
        F["Learn NVFlare"]
        G["Visualize and explore<br/>existing OMOP datasets"]
        H["Define site split and<br/>FedAvg baseline strategy"]
        I["Custom OMOP dataloader<br/>for NVFlare"]
        K["Simple linear model trained<br/>across simulated sites"]
        F --> G --> H --> I --> K
    end

    E -->|OMOP datasets| J["Joint pipeline:<br/>raw biobank to OMOP to federated training"]
    K -->|dataloader and model| J

    classDef sp1 fill:#dbeafe,stroke:#1e40af,color:#111827
    classDef sp2 fill:#dcfce7,stroke:#166534,color:#111827
    classDef gate fill:#fef3c7,stroke:#92400e,color:#111827
    classDef out fill:#ede9fe,stroke:#5b21b6,color:#111827
    class A,B,C,E sp1
    class F,G,H,I,K sp2
    class D gate
    class J out
```

## Programming language

Python is the preferred language for code in this repo.

## Subproject 1: An automated way of getting biobank data into OMOP 

### Goal

Come up with a skill or framework/tool to get ANY biobank dataset into OMOP.

### Milestones

- Agree on the minimal required tables & information of any biobanks for both datasets.
- Find ±2 datasets that have already been transferred to OMOP such as the UK Biobank.
- Write a skill or framework POC that recreates the existing conversions on its own to reproduce the conversion. This serves as our primary evaluation.
- Find ±3 datasets that are not yet in OMOP. Transform these into OMOP. They will serve as the eventual input for sub project 2.

## Subproject 2: A proof of concept of federated learning applied to OMOP datasets

### Goal

Implement a federated learning POC for OMOP data. Might require a new dataloader.

### Milestones

- Learn NVFlare as our federated learning tool
- Visualize & explore existing OMOP datasets. Try to understand how to best apply federated learning to them.
- Implement a custom dataloader for federated learning and apply it to a simple linear model. The accuracy or scientific outcome doesn't matter.
- Create a pipeline that brings together subproject 1 & 2

### Members 
-charles
-solvi
-lukas
-maria
-max
-nik
-mia
-kev

