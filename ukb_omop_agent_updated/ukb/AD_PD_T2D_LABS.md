# UKB agent workflow for AD, PD, T2D, and blood assays

This local run uses the **first 10,000 participants in the public UKB synthetic dataset**, as requested. It does not read HUNT Cloud data. The disease analysis is named **AD, PD, and T2D**. The source rules use UKB hospital inpatient ICD-10 field [41270](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=41270): AD includes `G30*` (Alzheimer’s disease) and `F00*` (dementia in Alzheimer’s disease), PD uses `G20*` (Parkinson’s disease), and T2D uses `E11*` (type 2 diabetes). The matching array position in field `41280` supplies each diagnosis date. These are narrow hospital-code proxies, not complete disease phenotypes; secondary Parkinsonism and unspecified diabetes are outside the named groups.

The five blood assays come from the public UKB field-group file `real_fields2.tsv`. Each uses mmol/L, a standard LOINC Measurement concept from the local Athena release, and assessment date field `53` for the same instance. Assessment date is a date proxy; it is not a verified blood-draw timestamp.

| Requested variable | UKB field | OMOP concept | LOINC |
|---|---:|---:|---|
| Blood glucose | [30740](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=30740) | 3013826 | 14749-6 |
| HDL cholesterol | [30760](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=30760) | 3023602 | 14646-4 |
| LDL direct | [30780](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=30780) | 42870529 | 69419-0 |
| Triglycerides | [30870](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=30870) | 3025839 | 14927-8 |
| Total cholesterol | [30690](https://biobank.ndph.ox.ac.uk/ukb/field.cgi?id=30690) | 3019900 | 14647-2 |

The mapping configuration is [`../specs/ukb_ad_pd_t2d_labs_v1.json`](../specs/ukb_ad_pd_t2d_labs_v1.json). All five measurements use standard unit concept `8753` (mmol/L), checked against the local Athena vocabulary. Person sex and birth year are included because the OMOP output needs Person rows. The Observation Period rule takes each person's earliest and latest retained, dated event. Its `period_type_concept_id` is `32880` (Standard algorithm), validated against the local Athena release. This is an inferred event span, **not verified UKB enrollment or continuous follow-up**. The selected data do not provide the linkage-coverage and censoring information needed to claim that events absent within the span did not occur.

From the repository root, run the existing Python workflow through the small Bash wrapper. Set `RUN_DIR` once so every step uses the same new output directory:

```bash
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh prepare
export RUN_DIR=solvi/results/my_new_run
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh inspect
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh propose
# Review proposed_disease_targets.csv and mapping_review.csv in this run.
# Record explicit approval of the reviewed targets before applying.
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh apply
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh qc
PYTHON=/opt/anaconda3/bin/python3 bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh plot
bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh summary
PYTHON=/opt/anaconda3/bin/python3 bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh disease-qc
PYTHON=/opt/anaconda3/bin/python3 bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh visual-qc
```

`prepare` reuses the existing 10,000-row UKB sample, downloads only the matching 10,000 rows of `real_fields2.tsv` when absent, checks EID alignment, and uses `make_ukb_subset.py` to create `solvi/data/ukb_10000/ukb_subset.tsv`. `inspect`, `apply`, and `qc` call the existing `ukb_omop_agent/general_agent.py`. `propose` writes one disease-focused Athena target per diagnosis code but **does not approve it**. Review the exact targets in `proposed_disease_targets.csv`. If the user explicitly approves the whole saved proposal, run `python3 ukb_omop_agent/ukb/approve_proposals.py --run "$RUN_DIR" --approve-proposed --evidence "user's stated approval"`; this checks the proposals against the Athena candidates and records the decisions. Otherwise edit `mapping_review.csv` to record only individually approved targets. Use a **fresh `RUN_DIR` for each inspection**; the commands reject a changed source, spec, diagnosis scope, or vocabulary. Set `UKB_SAMPLE_DIR` and `ATHENA_DIR` if your local paths differ. `plot` needs Matplotlib.

The current disease-focused run is in `solvi/results/run_004_observation_period/`. The user approved all 19 proposed ICD-10 source-code mappings in the previous run; the proposal file was unchanged, and `mapping_review.csv` records the carried-forward approvals. `analysis_summary.csv` reports AD (133 participants; 135 of 135 diagnosis records mapped), PD (38; 38 of 38), and T2D (185; 185 of 185). Each of the five assays has 10,356 of 10,356 source values mapped across 10,000 participants. Repeat assessments explain why an assay has more values than participants. Overall, 358 of 358 selected diagnosis records and 51,780 of 51,780 selected assay values have standard OMOP concept IDs; structural QC passed. The 10,000 Observation Period rows have type `32880`; 9,302 cover only one day. The saved `mapping_report.json` records the date rule and one-day count. **Do not use these periods as verified person-time or for incidence rates.** These percentages describe annotation coverage of the selected fields, not diagnostic accuracy or phenotype completeness. All `E11*` codes are rolled up to standard type 2 diabetes concept `201826`, so complication detail is lost.

`mapping_inventory.csv` lists each source diagnosis code and its assigned concept; `missing_codes.csv` shows unresolved codes (none in this run). The OMOP `person.csv`, `condition_occurrence.csv`, and `measurement.csv` files are in the run's `omop/` directory, alongside `graphical_qc.png` and `mapping_detail.png`. The QC files test structural replay and coverage, not clinical correctness. Synthetic UKB values can be internally inconsistent, as the [UKB synthetic dataset documentation](https://biobank.ndph.ox.ac.uk/ukb/exinfo.cgi?src=UKB_Synthetic_Dataset.html) explains.

`disease-qc` creates separate `disease_qc/AD_qc.png`, `PD_qc.png`, and `T2D_qc.png` figures. Each shows diagnosis and lab mapping coverage, the group's one-day Observation Period count, and assessment-year medians for **all five assays** (glucose, HDL, LDL direct, triglycerides, total cholesterol) with interquartile ranges and the participant count at each point. Filled points have at least five participants; hollow points have fewer and are not joined into a trend line. Each disease also gets `<disease>_codes.csv`, `<disease>_lab_coverage.csv`, and `<disease>_yearly_lab_trends.csv` for code-level mapping, assay coverage, and plotted values; combined CSVs are provided too. The lab coverage CSV flags zero provenance/source concept IDs and numeric `value_as_concept_id=0` separately from the successful target mappings. Disease groups are people ever coded with the respective ICD-10 family and may overlap. These yearly summaries compare different people assessed in each year; they are **not within-person trajectories or disease effects**. Lab dates remain assessment-date proxies, and UKB synthetic values should not be interpreted clinically.

`visual-qc` creates `visual_qc/all_variable_distributions.png` with the number of participants who do or do not have a selected AD, PD, or T2D code, plus histograms of all five assay values. “No selected code” does not establish that a participant is disease-free. It also creates `AD_distributions.png`, `PD_distributions.png`, and `T2D_distributions.png` to show the five assay distributions among participants with each selected diagnosis. Histograms include repeat assessments; dashed lines mark medians. `variable_distribution_summary.csv` records counts, quartiles, and negative values. Negative synthetic concentrations are displayed and counted as QC findings, not silently removed.

The same command creates `visual_qc/observation_period_qc.png`, which shows period-length categories, the one-day share for the full cohort and each disease group, and checks for missing periods, invalid dates, and events outside their period. `observation_period_summary.csv` and `observation_period_checks.json` contain the underlying numbers. In the current run, 9,302 of 10,000 periods are one day, while none of the AD, PD, or T2D group periods is one day because the selected diagnosis dates extend their event spans. This illustrates how strongly the technical period depends on which events were retained; it does **not** establish observed person-time or true UKB follow-up. PNG and PDF versions of all figures are saved locally.

`solvi/data/` and `solvi/results/` are local generated outputs and are ignored by Git; reusable code and configuration live under `ukb_omop_agent/`.
