#!/usr/bin/env python3
"""Audit UKB pilot ETL fidelity and concept coverage against source and Athena."""

import argparse
import collections
import csv
import datetime as dt
import json
import re
from pathlib import Path


COLUMN = re.compile(r"^(\d+)-(\d+)\.(\d+)$")
TARGETS = {
    "21001": ("3038553", "9531"),
    "4080": ("3004249", "8876"),
}


def read_table(path, delimiter=","):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=delimiter)
        return reader.fieldnames, list(reader)


def valid_date(value):
    try:
        return dt.date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def source_events(path, e11_only=False):
    header, rows = read_table(path, "\t")
    if not header or header[0] != "EID":
        raise ValueError("Expected a UKB source TSV beginning with EID")
    fields = collections.defaultdict(list)
    for name in header:
        match = COLUMN.fullmatch(name)
        if match:
            fields[match.group(1)].append((int(match.group(2)), int(match.group(3)), name))
    dates = {(i, a): name for i, a, name in fields["53"]}
    diagnosis_dates = {(i, a): name for i, a, name in fields["41280"]}
    people = {}
    measurement = collections.Counter()
    condition = collections.Counter()
    exclusions = collections.Counter()
    source_clinical_values = collections.Counter()
    for row in rows:
        eid = row["EID"]
        if eid in people:
            raise ValueError(f"Duplicate source EID: {eid}")
        people[eid] = {
            "gender": {"0": "8532", "1": "8507"}.get(row["31-0.0"].strip(), "0"),
            "birth_year": row["34-0.0"].strip(),
        }
        for field_id, (concept, unit) in TARGETS.items():
            for instance, array, name in fields[field_id]:
                value = row[name].strip()
                if not value:
                    continue
                source_clinical_values[field_id] += 1
                date_col = dates.get((instance, 0))
                date = valid_date(row[date_col]) if date_col else None
                if date is None:
                    exclusions["measurement_no_valid_date"] += 1
                    continue
                try:
                    number = float(value)
                    if not -float("inf") < number < float("inf"):
                        raise ValueError
                except ValueError:
                    exclusions["measurement_not_numeric"] += 1
                    continue
                measurement[(eid, field_id, date, value, concept, unit)] += 1
        for instance, array, name in fields["41270"]:
            code = row[name].strip()
            if not code:
                continue
            is_e11 = code.upper().replace(".", "").startswith("E11")
            if e11_only and not is_e11:
                continue
            source_clinical_values["41270"] += 1
            date_col = diagnosis_dates.get((instance, array))
            if date_col is None:
                exclusions["diagnosis_no_date_column"] += 1
                continue
            date = valid_date(row[date_col])
            if date is None:
                exclusions["diagnosis_no_valid_date"] += 1
                continue
            concept = "201826" if is_e11 else "0"
            condition[(eid, code, date, concept)] += 1
    return people, measurement, condition, exclusions, source_clinical_values


def compare_multisets(expected, actual):
    matched = sum((expected & actual).values())
    return {
        "expected": sum(expected.values()),
        "actual": sum(actual.values()),
        "matched": matched,
        "missing": sum((expected - actual).values()),
        "extra": sum((actual - expected).values()),
    }


def concept_check(vocabulary_dir, people, measurements, conditions, periods):
    usage = collections.Counter()
    expected_domains = {}
    for row in people:
        concept = row["gender_concept_id"]
        if concept != "0":
            usage[concept] += 1
            expected_domains[concept] = "Gender"
    for row in measurements:
        for col, domain in (("measurement_concept_id", "Measurement"), ("unit_concept_id", "Unit")):
            concept = row[col]
            if concept != "0":
                usage[concept] += 1
                expected_domains[concept] = domain
    for row in conditions:
        concept = row["condition_concept_id"]
        if concept != "0":
            usage[concept] += 1
            expected_domains[concept] = "Condition"
    for table in (periods, measurements, conditions):
        for row in table:
            col = next((key for key in row if key.endswith("_type_concept_id")), None)
            if col and row[col] != "0":
                usage[row[col]] += 1
                expected_domains[row[col]] = "Type Concept"
    found = {}
    with (vocabulary_dir / "CONCEPT.csv").open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            if row["concept_id"] in usage:
                found[row["concept_id"]] = row
            if len(found) == len(usage):
                break
    invalid = []
    for concept, domain in expected_domains.items():
        row = found.get(concept)
        if row is None or row["domain_id"] != domain or row["standard_concept"] != "S" or row["invalid_reason"]:
            invalid.append(concept)
    return {"nonzero_concepts": len(usage), "invalid_or_wrong_domain": invalid,
            "usage_by_concept": dict(sorted(usage.items(), key=lambda x: int(x[0])))}


def audit(source, omop, vocabulary, e11_only=False):
    source_people, expected_measurements, expected_conditions, exclusions, source_values = source_events(source, e11_only)
    _, people = read_table(omop / "person.csv")
    _, periods = read_table(omop / "observation_period.csv")
    _, measurements = read_table(omop / "measurement.csv")
    _, conditions = read_table(omop / "condition_occurrence.csv")
    table_rows = {
        "person": people,
        "observation_period": periods,
        "measurement": measurements,
        "condition_occurrence": conditions,
    }
    key_failures = {}
    for name, rows in table_rows.items():
        ids = [row[f"{name}_id"] for row in rows]
        key_failures[name] = len(ids) - len(set(ids)) + sum(not value for value in ids)
    id_to_eid = {row["person_id"]: row["person_source_value"] for row in people}
    unique_ids = len(id_to_eid) == len(people)
    unique_eids = len(set(id_to_eid.values())) == len(people)
    orphan_rows = sum(
        row["person_id"] not in id_to_eid
        for rows in (periods, measurements, conditions) for row in rows
    )
    placeholder_mismatches = (
        sum(row["race_concept_id"] != "0" or row["ethnicity_concept_id"] != "0" for row in people)
        + sum(row["period_type_concept_id"] != "0" for row in periods)
        + sum(row["measurement_type_concept_id"] != "0" for row in measurements)
        + sum(row["condition_type_concept_id"] != "0" for row in conditions)
    )
    person_match = sum(
        row["person_source_value"] in source_people
        and row["gender_concept_id"] == source_people[row["person_source_value"]]["gender"]
        and row["year_of_birth"] == source_people[row["person_source_value"]]["birth_year"]
        for row in people
    )
    actual_measurements = collections.Counter((
        id_to_eid.get(row["person_id"], "<missing>"), row["measurement_source_value"],
        row["measurement_date"], row["value_as_number"],
        row["measurement_concept_id"], row["unit_concept_id"]
    ) for row in measurements)
    actual_conditions = collections.Counter((
        id_to_eid.get(row["person_id"], "<missing>"), row["condition_source_value"],
        row["condition_start_date"], row["condition_concept_id"]
    ) for row in conditions)
    m_compare = compare_multisets(expected_measurements, actual_measurements)
    c_compare = compare_multisets(expected_conditions, actual_conditions)
    event_dates = collections.defaultdict(list)
    for row in measurements:
        event_dates[row["person_id"]].append(row["measurement_date"])
    for row in conditions:
        event_dates[row["person_id"]].append(row["condition_start_date"])
    period_mismatch = sum(
        not event_dates[row["person_id"]]
        or row["observation_period_start_date"] != min(event_dates[row["person_id"]])
        or row["observation_period_end_date"] != max(event_dates[row["person_id"]])
        for row in periods
    )
    period_person_ids = [row["person_id"] for row in periods]
    period_mismatch += len(periods) - len(set(period_person_ids))
    period_mismatch += len(set(event_dates) - set(period_person_ids))
    concepts = concept_check(vocabulary, people, measurements, conditions, periods)
    bmi = [float(row["value_as_number"]) for row in measurements if row["measurement_source_value"] == "21001"]
    sbp = [float(row["value_as_number"]) for row in measurements if row["measurement_source_value"] == "4080"]
    gender_mapped = sum(row["gender_concept_id"] != "0" for row in people)
    measurement_mapped = sum(row["measurement_concept_id"] != "0" for row in measurements)
    condition_mapped = sum(row["condition_concept_id"] != "0" for row in conditions)
    annotation_by_field = {}
    diagnosis_label = "E11 diagnoses" if e11_only else "ICD-10 diagnoses"
    for field_id, label in (("21001", "BMI"), ("4080", "Systolic BP"), ("41270", diagnosis_label)):
        records = (conditions if field_id == "41270" else
                   [row for row in measurements if row["measurement_source_value"] == field_id])
        concept_column = "condition_concept_id" if field_id == "41270" else "measurement_concept_id"
        standard = sum(row[concept_column] != "0" for row in records)
        annotation_by_field[label] = {
            "source_values": source_values[field_id],
            "standard_concept": standard,
            "retained_with_concept_0": len(records) - standard,
            "not_exported": source_values[field_id] - len(records),
        }
    annotation_total = {
        key: sum(values[key] for values in annotation_by_field.values())
        for key in ("source_values", "standard_concept", "retained_with_concept_0", "not_exported")
    }
    if annotation_total["standard_concept"] + annotation_total["retained_with_concept_0"] + annotation_total["not_exported"] != annotation_total["source_values"]:
        raise ValueError("Source-value annotation categories do not reconcile")
    issues = []
    if not unique_ids or not unique_eids or set(id_to_eid.values()) != set(source_people) or person_match != len(source_people):
        issues.append("person table does not exactly match source IDs, gender and birth year")
    if m_compare["missing"] or m_compare["extra"]:
        issues.append("measurement records differ from eligible source readings")
    if c_compare["missing"] or c_compare["extra"]:
        issues.append("condition records differ from dated source diagnoses or E11 pilot rule")
    if period_mismatch or len(periods) != len(source_people):
        issues.append("observation periods do not match the pilot event-date proxy")
    if concepts["invalid_or_wrong_domain"]:
        issues.append("nonzero concept IDs are invalid, nonstandard or in the wrong domain")
    if any(key_failures.values()) or orphan_rows:
        issues.append("duplicate/empty row IDs or orphan person references")
    if placeholder_mismatches:
        issues.append("unresolved race, ethnicity or type fields differ from the pilot's concept-0 rule")
    interpretation = [
        "Rule fidelity tests whether the ETL follows the declared pilot rules; it is not a clinical gold-standard accuracy score.",
        "The E11 rollup loses complication detail. Its clinical correctness needs an independent reviewed crosswalk.",
        "The source is synthetic and E11-positive participants were deliberately selected; prevalence and predictive performance cannot be inferred.",
    ]
    if not e11_only:
        interpretation.insert(1, "Only E11-family diagnosis codes are mapped to a standard concept; other exported diagnosis codes remain at concept 0.")
    return {
        "source": str(source), "omop": str(omop),
        "diagnosis_scope": "e11_only" if e11_only else "all_dated_codes",
        "rule_fidelity": {
            "person": {"matched": person_match, "expected": len(source_people),
                       "actual": len(people)},
            "measurement": m_compare,
            "condition_occurrence": c_compare,
            "observation_period": {"expected": len(source_people),
                                   "actual": len(periods), "mismatches": period_mismatch},
        },
        "concept_coverage": {
            "gender": {"mapped": gender_mapped, "total": len(people)},
            "measurement": {"mapped": measurement_mapped, "total": len(measurements)},
            "condition_occurrence": {"mapped": condition_mapped, "total": len(conditions)},
        },
        "source_clinical_value_annotation": {"by_field": annotation_by_field, "total": annotation_total},
        "athena_concepts": concepts,
        "structural_checks": {
            "key_failures": key_failures,
            "orphan_person_references": orphan_rows,
            "placeholder_field_mismatches": placeholder_mismatches,
        },
        "source_exclusions": dict(sorted(exclusions.items())),
        "value_qc": {
            "bmi": {"total": len(bmi), "outside_10_to_80": sum(v < 10 or v > 80 for v in bmi)},
            "sbp": {"total": len(sbp), "outside_60_to_250": sum(v < 60 or v > 250 for v in sbp)},
        },
        "issues": issues,
        "interpretation": interpretation,
    }


def markdown(report):
    fidelity = report["rule_fidelity"]
    coverage = report["concept_coverage"]
    pct = lambda a, b: f"{100 * a / b:.2f}%" if b else "n/a"
    lines = ["# UKB pilot mapping evaluation", "",
             f"**Rule-fidelity verdict:** {'PASS' if not report['issues'] else 'FAIL'}",
             "", "| Check | Matched / expected | Missing | Extra |", "| --- | ---: | ---: | ---: |"]
    p = fidelity["person"]
    lines.append(f"| Person fields | {p['matched']}/{p['expected']} | {p['expected']-p['matched']} | {p['actual']-p['matched']} |")
    for name, label in (("measurement", "Measurements"), ("condition_occurrence", "Dated diagnoses")):
        x = fidelity[name]
        lines.append(f"| {label} | {x['matched']}/{x['expected']} | {x['missing']} | {x['extra']} |")
    x = fidelity["observation_period"]
    lines.append(f"| Event-date periods | {x['actual']}/{x['expected']} | {x['mismatches']} mismatches | — |")
    lines += ["", "| Standard-concept coverage | Mapped / total | Rate |",
              "| --- | ---: | ---: |"]
    for name, label in (("gender", "Gender"), ("measurement", "Measurements"),
                        ("condition_occurrence", "Dated diagnoses")):
        x = coverage[name]
        lines.append(f"| {label} | {x['mapped']}/{x['total']} | {pct(x['mapped'],x['total'])} |")
    diagnosis_label = "E11 code" if report["diagnosis_scope"] == "e11_only" else "ICD-10 code"
    lines += ["", "## Annotation of provided clinical values", "",
              f"This denominator counts nonempty BMI, systolic-pressure and {diagnosis_label} values in the selected UKB cohort. "
              "Sex, birth year and date fields are evaluated separately as attributes or event dates.", "",
              "| UKB source field | Source values | Standard concept | Retained as concept 0 | Not exported |",
              "| --- | ---: | ---: | ---: | ---: |"]
    annotated = report["source_clinical_value_annotation"]
    for label, x in annotated["by_field"].items():
        lines.append(f"| {label} | {x['source_values']:,} | {x['standard_concept']:,} | "
                     f"{x['retained_with_concept_0']:,} | {x['not_exported']:,} |")
    x = annotated["total"]
    lines.append(f"| **Total** | **{x['source_values']:,}** | **{x['standard_concept']:,} "
                 f"({pct(x['standard_concept'], x['source_values'])})** | "
                 f"**{x['retained_with_concept_0']:,}** | **{x['not_exported']:,}** |")
    lines += ["", f"Athena concepts used: {report['athena_concepts']['nonzero_concepts']} nonzero IDs; "
              f"invalid or wrong-domain: {len(report['athena_concepts']['invalid_or_wrong_domain'])}.",
              f"Structural checks: {sum(report['structural_checks']['key_failures'].values())} key failures, "
              f"{report['structural_checks']['orphan_person_references']} orphan person references, "
              f"{report['structural_checks']['placeholder_field_mismatches']} placeholder-field mismatches.",
              "", "| Source or value QC | Count |", "| --- | ---: |"]
    for key, value in report["source_exclusions"].items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")
    v = report["value_qc"]
    lines.append(f"| BMI outside 10–80 kg/m² | {v['bmi']['outside_10_to_80']}/{v['bmi']['total']} |")
    lines.append(f"| SBP outside 60–250 mmHg | {v['sbp']['outside_60_to_250']}/{v['sbp']['total']} |")
    lines += ["", "## Interpretation", ""]
    lines += [f"- {item}" for item in report["interpretation"]]
    if report["issues"]:
        lines += ["", "## Rule-fidelity failures", ""]
        lines += [f"- {item}" for item in report["issues"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/ukb_pilot/ukb_subset.tsv"))
    parser.add_argument("--omop", type=Path, default=Path("data/ukb_omop_pilot/all"))
    parser.add_argument("--vocabulary", type=Path, default=Path("../OMOP_complete"))
    parser.add_argument("--output", type=Path, default=Path("data/ukb_omop_pilot/qc"))
    parser.add_argument("--e11-only", action="store_true", help="Evaluate only E11-family diagnosis records")
    args = parser.parse_args()
    report = audit(args.source, args.omop, args.vocabulary, args.e11_only)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "mapping_evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "mapping_evaluation.md").write_text(markdown(report))
    print(markdown(report))
    if report["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
