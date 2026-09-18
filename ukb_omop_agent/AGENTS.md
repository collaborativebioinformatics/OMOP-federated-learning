# UKB-to-OMOP mapping agent

Work only on the UK Biobank synthetic extract and the local OMOP vocabulary supplied by the user. This folder is independent of `omop_skill`.

1. Inspect the source schema and pair each diagnosis field `41270-i.a` with date field `41280-i.a`. Retain the original code and EID for traceability. Never infer a diagnosis date from another array position.
2. Identify the source coding system before proposing an OMOP target. UKB field 41270 is ICD-10 coded; do not silently treat it as ICD10CM. If the matching source vocabulary is absent from Athena, report that limitation and leave targets blank.
3. Map source codes only to valid, standard OMOP concepts using a documented vocabulary relationship or an explicitly reviewed local mapping. Preserve all approved `Maps to` targets; one source diagnosis can yield multiple OMOP rows. Record whether a reviewed mapping is exact, a broad rollup, or a vocabulary relationship. Do not guess from text similarity.
4. Keep unmapped source diagnoses with `condition_concept_id = 0` and their `condition_source_value`. Report mapping coverage using dated source records and distinct source codes as separate denominators.
5. Run `general_agent.py qc`, `plot_qc.py`, and the tests after changing a mapping. The current QC replays the selected input and review file and supports multiple approved targets. Treat coverage as technical annotation, not clinical correctness. Review broad rollups and date/provenance assumptions separately.

Use `README.md` for commands and `general_agent.py` for the configuration-driven inspect/apply/QC workflow. The narrower `agent.py` is retained as a legacy pilot. Keep source-specific assumptions in a versioned specification. Do not modify `omop_skill` or its separate contract as a shortcut.

## Conversational workflow in Codex

This Codex task is the LLM interface; when Codex is signed in with ChatGPT, it needs no separate API key. When asked to prepare synthetic UKB input, run `ukb/sample_ukb_fields.py --rows 10000` (reusing its verified existing sample), then `ukb/make_ukb_subset.py --all-matching --diagnosis-prefix CODE` or `--diagnosis-code CODE`. For an E11 cohort, `--all-e11` remains available. Report the selected participant count and output path. Do not start the complete-file download when a 10,000-row sample meets the request.

If the user names a diagnosis rather than providing a code, search ICD10 concept names in the user's local Athena `CONCEPT.csv` and identify candidate codes or families. Before subsetting, inspection, or mapping, show the proposed disease selection and ask the user to validate it explicitly. State the disease label, source vocabulary, exact code or prefix, description, whether it selects one code or a family, and any material exclusions. For example: “Validate T2D as UKB ICD-10 `E11*` (type 2 diabetes mellitus and its subcodes); this excludes `E10*` and other diabetes families.” Do not treat silence, an LLM recommendation, or OMOP target approval as validation of the source disease-code selection. If the user already explicitly supplied or validated the exact selection in the current conversation, preserve that validation and do not ask again.

Never silently substitute ICD10CM or guess a code from the disease name. Use the validated code or prefix for both subsetting and `general_agent.py` inspection; the source subset and mapping filter are separate operations. Source-code validation is a separate checkpoint from approving the resulting OMOP target concepts.

Present Athena mapping candidates with their concept IDs, names, domains, and source-code relationships. A user's explicit conversational approval of a specified source code and target concept is authorization to update that row in `mapping_review.csv`; validate the target against the candidate list or require documented `local_reviewed` evidence. Record the user's stated evidence and preserve all other rows. Do not turn a model recommendation alone into an approval. Then run apply, QC, and graphical QC on the reviewed file.
