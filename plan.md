# Plan

Three days, eight people, two subprojects. This is what we settled on wednesday.
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

charles working on the output data structure that subproject 2 is using.

mia and mira working on QC and quality control for comparing our automatic omop to etl synthea.

Nik and Max are working on ETL and Synthea data generation.


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
| 13:30 | Writers meeting | Mira volunteered to be writer
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
