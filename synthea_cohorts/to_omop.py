from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

EHR_RECORD = 32817
NO_MATCHING_CONCEPT = 0
GENDER = {"M": 8507, "F": 8532}
UNITS = {"kg/m2": 9531, "mm[Hg]": 8876, "mg/dL": 8840, "%": 8554, "mL/min/{1.73_m2}": 8795}

TABLES = ("person", "observation_period", "measurement", "condition_occurrence")


def _read(directory: Path, name: str) -> str:
    for suffix in (".csv", ".csv.gz"):
        candidate = directory / f"{name}{suffix}"
        if candidate.exists():
            return f"read_csv('{candidate}')"
    raise FileNotFoundError(f"no {name} table under {directory}")


def convert(cohort: Path, out: Path, sites: int) -> dict[str, int]:
    """Map a normalized Synthea cohort to the OMOP tables of the data contract.

    Args:
        cohort: Directory holding ``patients``, ``diagnoses`` and ``biomarkers``.
        out: Directory to write ``<site>/`` folders into.
        sites: Number of sites to split the patients across.

    Returns:
        Row counts per site.

    Raises:
        ValueError: If ``sites`` is not positive.
    """
    if sites <= 0:
        raise ValueError(f"sites must be positive, got {sites}")

    connection = duckdb.connect()
    connection.execute(f"create view patients as select * from {_read(cohort, 'patients')}")
    connection.execute(f"create view diagnoses as select * from {_read(cohort, 'diagnoses')}")
    connection.execute(f"create view biomarkers as select * from {_read(cohort, 'biomarkers')}")

    units = ", ".join(f"('{source}', {concept})" for source, concept in UNITS.items())
    genders = ", ".join(f"('{source}', {concept})" for source, concept in GENDER.items())
    connection.execute(f"create view unit_map as select * from (values {units}) t(source, concept_id)")
    connection.execute(f"create view gender_map as select * from (values {genders}) t(source, concept_id)")

    connection.execute(
        f"""
        create view person_keyed as
        select
            row_number() over (order by patient_id) as person_id,
            patient_id,
            coalesce(g.concept_id, {NO_MATCHING_CONCEPT}) as gender_concept_id,
            cast(strftime(cast(p.birth_date as date), '%Y') as integer) as year_of_birth,
            abs(hash(patient_id)) % {sites} as site
        from patients p
        left join gender_map g on g.source = p.sex
        """
    )

    counts: dict[str, int] = {}
    for site in range(sites):
        name = f"site_{chr(ord('a') + site)}" if sites <= 26 else f"site_{site:03d}"
        directory = out / name
        directory.mkdir(parents=True, exist_ok=True)
        where = f"where site = {site}"

        connection.execute(
            f"""copy (
                select person_id, gender_concept_id, year_of_birth,
                       {NO_MATCHING_CONCEPT} as race_concept_id,
                       {NO_MATCHING_CONCEPT} as ethnicity_concept_id,
                       patient_id as person_source_value
                from person_keyed {where} order by person_id
            ) to '{directory / "person.csv"}' (header, delimiter ',')"""
        )
        connection.execute(
            f"""copy (
                select row_number() over (order by k.person_id) as observation_period_id,
                       k.person_id,
                       min(e.event_date) as observation_period_start_date,
                       max(e.event_date) as observation_period_end_date,
                       {EHR_RECORD} as period_type_concept_id
                from person_keyed k
                join (
                    select patient_id, cast(measurement_date as date) as event_date from biomarkers
                    union all
                    select patient_id, cast(diagnosis_start_date as date) from diagnoses
                ) e on e.patient_id = k.patient_id
                {where} group by k.person_id order by k.person_id
            ) to '{directory / "observation_period.csv"}' (header, delimiter ',')"""
        )
        connection.execute(
            f"""copy (
                select row_number() over (order by k.person_id, b.measurement_date) as measurement_id,
                       k.person_id,
                       b.omop_measurement_concept_id as measurement_concept_id,
                       cast(b.measurement_date as date) as measurement_date,
                       {EHR_RECORD} as measurement_type_concept_id,
                       b.value_as_number,
                       coalesce(u.concept_id, {NO_MATCHING_CONCEPT}) as unit_concept_id,
                       b.source_code as measurement_source_value
                from person_keyed k
                join biomarkers b on b.patient_id = k.patient_id
                left join unit_map u on u.source = b.unit_source_value
                {where} and b.omop_measurement_concept_id is not null and b.omop_measurement_concept_id > 0
            ) to '{directory / "measurement.csv"}' (header, delimiter ',')"""
        )
        connection.execute(
            f"""copy (
                select row_number() over (order by k.person_id, d.diagnosis_start_date) as condition_occurrence_id,
                       k.person_id,
                       d.omop_condition_concept_id as condition_concept_id,
                       cast(d.diagnosis_start_date as date) as condition_start_date,
                       {EHR_RECORD} as condition_type_concept_id,
                       d.source_code as condition_source_value
                from person_keyed k
                join diagnoses d on d.patient_id = k.patient_id
                {where} and d.omop_condition_concept_id is not null and d.omop_condition_concept_id > 0
            ) to '{directory / "condition_occurrence.csv"}' (header, delimiter ',')"""
        )
        counts[name] = int(connection.execute(f"select count(*) from person_keyed {where}").fetchone()[0])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a normalized Synthea cohort to OMOP contract tables.")
    parser.add_argument("cohort", type=Path, help="directory with patients, diagnoses and biomarkers")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sites", type=int, default=3)
    args = parser.parse_args()
    for name, people in convert(args.cohort, args.output, args.sites).items():
        print(f"{name}: {people} people -> {args.output / name}")


if __name__ == "__main__":
    main()
