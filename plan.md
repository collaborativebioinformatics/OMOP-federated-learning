# Plan

Three days, eight people, two subprojects. This is what we settled on Thursday morning.
If something here is wrong, change it in the repo instead of in your head.

The goal is one working minimal working path end to end.

## Scope

One source dataset: Synthea.

Synthea produces raw CSV that is not OMOP. OHDSI maintains ETL-Synthea, which converts
it. That conversion is our reference. We measure our automated version against it, and
that comparison is the scientific claim of the project.

The two sites for the federated part come from the same generator with different
demographics. We say that openly in the presentation. Nobody is pretending these are
two biobanks.

Dropped on purpose: multiple biobanks, real UK Biobank data, anything needing an access
application. Three days is not enough and access alone would eat both of them.

## Minimal table set

Agreed Thursday 09:00. Subproject 2 builds against this and nothing else.

for example 
| Table | Fields |
| --- | --- |
| person | person_id, year_of_birth, gender_concept_id |
| observation_period | person_id, start_date, end_date |
| condition_occurrence | person_id, condition_concept_id, condition_start_date |
| measurement | person_id, measurement_concept_id, value_as_number, measurement_date |

Four tables, twelve columns. Everything else is v2.

Output format: one parquet file per table, one directory per site.

## Work areas

Subproject 1, source to OMOP.

1. **Source profiling.** What is actually in the Synthea CSVs: tables, fields, value
   ranges, how often something is empty, how many distinct codes. White Rabbit produces
   the scan report. Owner:
2. **Structural mapping.** Which source field becomes which OMOP field. Rabbit-in-a-Hat
   reads the scan report and exports a mapping specification. Owner:
3. **Automated mapping proposal.** The model gets the scan report and proposes the
   structural and code mappings on its own. This is the part that makes the project
   interesting, and it gets measured against the same reference as everything else.
   Owner:
4. **Vocabulary mapping and clinical sign-off.** Every distinct source code needs a
   standard concept. Usagi proposes candidates by text similarity, a human decides.
   Owner:
5. **ETL code.** Reads the source, applies both mappings, writes the four tables.
   Person ids, date handling, deriving observation_period. Owner:
6. **Validation.** Row counts per table, share of records that end up on concept_id 0,
   the ten most frequent unmapped codes. Ours against the reference, side by side.
   Owner:

Subproject 2, federated learning.

7. **Reference run.** Get ETL-Synthea running end to end on 100 patients. One person,
   nothing else, until it works. Without it we have no reference and no evaluation.
   Owner:
8. **Dataloader.** Reads the four tables per site and feeds NVFlare. Owner:
9. **Model.** A linear model over age, sex and a handful of condition flags. Accuracy
   does not matter. The point is that the data never moves. Owner:

## Milestones

Thursday

| Time | What |
| --- | --- |
| 10:00 | Table set fixed and committed |
| 11:00 | Fake data generator writes the four tables for two sites. Subproject 2 is now independent of subproject 1 |
| 12:00 | ETL-Synthea runs end to end on 100 patients |
| 13:30 | Writers meeting |
| 14:00 | Mid-term slides done |
| 14:30 | Mid-term presentation |
| 17:00 | Linear model trains federated on the fake data |

Friday

| Time | What |
| --- | --- |
| 10:00 | Real converted data replaces the fake data on the same path |
| 13:00 | Feature freeze. Anything not running at 13:00 does not go on a slide |
| 14:00 | Slides done |
| 15:30 | Results presentation |

## How we know it worked

Two numbers, both per table.

Share of records on concept_id 0, ours against ETL-Synthea. Row count difference, ours
against ETL-Synthea.

Plus the ten most frequent codes we failed to map, because that is the list that tells
the next person what to fix.

## If the reference fails

If ETL-Synthea is not running by 12:00 on Thursday, we fall back to Eunomia as a
ready-made OMOP database. Subproject 2 continues unchanged. We lose the evaluation,
and we say so in the presentation instead of hiding it.

## Working agreement

Commit small and often. Broken code in the repo beats working code on a laptop.

If you are blocked for more than twenty minutes, say so out loud. Someone at this table
has hit it before.

Remote people get the link before the discussion starts, not after.
