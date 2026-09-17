# UKB-to-OMOP mapping agent

Work only on the UK Biobank synthetic extract and the local OMOP vocabulary supplied by the user. This folder is independent of `omop_skill`.

1. Inspect the source schema and pair each diagnosis field `41270-i.a` with date field `41280-i.a`. Retain the original code and EID for traceability. Never infer a diagnosis date from another array position.
2. Identify the source coding system before proposing an OMOP target. UKB field 41270 is ICD-10 coded; do not silently treat it as ICD10CM. If the matching source vocabulary is absent from Athena, report that limitation and leave targets blank.
3. Map source codes only to valid, standard OMOP concepts using a documented vocabulary relationship or an explicitly reviewed local mapping. Preserve all approved `Maps to` targets; one source diagnosis can yield multiple OMOP rows. Record whether a reviewed mapping is exact, a broad rollup, or a vocabulary relationship. Do not guess from text similarity.
4. Keep unmapped source diagnoses with `condition_concept_id = 0` and their `condition_source_value`. Report mapping coverage using dated source records and distinct source codes as separate denominators.
5. Run `general_agent.py qc`, `plot_qc.py`, and the tests after changing a mapping. The current QC replays the selected input and review file and supports multiple approved targets. Treat coverage as technical annotation, not clinical correctness. Review broad rollups and date/provenance assumptions separately.

Use `README.md` for commands and `general_agent.py` for the configuration-driven inspect/apply/QC workflow. The narrower `agent.py` is retained as a legacy pilot. Keep source-specific assumptions in a versioned specification. Do not modify `omop_skill` or its separate contract as a shortcut.
