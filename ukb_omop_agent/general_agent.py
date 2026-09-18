#!/usr/bin/env python3
"""Configuration-driven source-to-OMOP pilot with Athena proposals and replay QC."""

import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path


DOMAINS = {
    "Condition": ("condition_occurrence", "condition_concept_id", "condition_start_date"),
    "Measurement": ("measurement", "measurement_concept_id", "measurement_date"),
    "Observation": ("observation", "observation_concept_id", "observation_date"),
    "Procedure": ("procedure_occurrence", "procedure_concept_id", "procedure_date"),
}
HEADERS = {
    "person": ["person_id", "gender_concept_id", "year_of_birth", "race_concept_id", "ethnicity_concept_id", "person_source_value"],
    "observation_period": ["observation_period_id", "person_id", "observation_period_start_date", "observation_period_end_date", "period_type_concept_id"],
    "condition_occurrence": ["condition_occurrence_id", "person_id", "condition_concept_id", "condition_start_date", "condition_type_concept_id", "condition_source_value", "condition_source_concept_id"],
    "measurement": ["measurement_id", "person_id", "measurement_concept_id", "measurement_date", "measurement_type_concept_id", "value_as_number", "value_as_concept_id", "unit_concept_id", "measurement_source_value", "measurement_source_concept_id"],
    "observation": ["observation_id", "person_id", "observation_concept_id", "observation_date", "observation_type_concept_id", "value_as_number", "value_as_concept_id", "unit_concept_id", "observation_source_value", "observation_source_concept_id"],
    "procedure_occurrence": ["procedure_occurrence_id", "person_id", "procedure_concept_id", "procedure_date", "procedure_type_concept_id", "procedure_source_value", "procedure_source_concept_id"],
}
REVIEW_COLUMNS = ["field_id", "source_vocabulary", "source_code", "records", "dated_records",
                  "source_concept_ids", "candidate_targets_json", "candidate_value_ids",
                  "decision", "approved_target_ids", "approved_value_concept_id", "mapping_kind", "evidence"]
UKB_FIELD = re.compile(r"^(\d+)-(\d+)\.(\d+)$")
TODAY = dt.date.today().strftime("%Y%m%d")


def read_spec(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema_version") != 1 or spec.get("adapter") not in {"ukb_wide", "flat_events"}:
        raise ValueError("Expected schema_version 1 and adapter ukb_wide or flat_events")
    if not spec.get("person", {}).get("id_column"):
        raise ValueError("Spec lacks person.id_column")
    if spec["adapter"] == "ukb_wide" and not spec.get("fields"):
        raise ValueError("UKB spec lacks fields")
    for field in spec.get("fields", []):
        if field.get("target_concept_ids") and not field.get("evidence"):
            raise ValueError(f"Local mapping for {field.get('field_id')} lacks evidence")
    if spec["adapter"] == "flat_events" and not spec.get("columns"):
        raise ValueError("Flat-events spec lacks columns")
    return spec


def source_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spec_digest(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()


def vocabulary_release(vocabulary):
    with (vocabulary / "VOCABULARY.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["vocabulary_id"] == "None":
                return row["vocabulary_version"]
    raise ValueError("VOCABULARY.csv has no OMOP release row")


def date_value(raw):
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw.strip()[:10]).isoformat()
    except ValueError:
        return None


def numeric_value(raw):
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return raw if math.isfinite(value) else None


def normalize(code, mode):
    value = code.strip().upper()
    if mode == "icd10_dots":
        return value.replace(".", "")
    if mode == "exact_casefold":
        return value
    raise ValueError(f"Unsupported code_normalization: {mode}")


def _add_person(people, source_id, row, config):
    if not source_id:
        raise ValueError("Missing person ID")
    sex = row.get(config.get("sex_column", ""), "").strip()
    birth = row.get(config.get("birth_year_column", ""), "").strip()
    if birth and (not birth.isdigit() or len(birth) != 4):
        raise ValueError(f"Invalid birth year for {source_id}: {birth!r}")
    record = (int(config.get("sex_map", {}).get(sex, 0)), birth)
    if source_id in people and people[source_id] != record:
        raise ValueError(f"Conflicting demographics for {source_id}")
    people[source_id] = record


def extract(source, spec):
    """Turn one supported source layout into common event dictionaries."""
    people, events, exclusions = {}, [], collections.Counter()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=spec.get("delimiter", ","))
        header = set(reader.fieldnames or [])
        person_config = spec["person"]
        if person_config["id_column"] not in header:
            raise ValueError(f"Missing person ID column {person_config['id_column']}")
        if spec["adapter"] == "ukb_wide":
            field_columns = collections.defaultdict(list)
            for name in reader.fieldnames:
                match = UKB_FIELD.fullmatch(name)
                if match:
                    field_columns[match.group(1)].append((int(match.group(2)), int(match.group(3)), name))
            for field in spec["fields"]:
                if not field_columns.get(field["field_id"]):
                    raise ValueError(f"No columns for UKB field {field['field_id']}")
            for row in reader:
                source_id = row[person_config["id_column"]].strip()
                if source_id in people:
                    raise ValueError(f"Duplicate person ID {source_id}")
                _add_person(people, source_id, row, person_config)
                for field in spec["fields"]:
                    for instance, array, name in field_columns[field["field_id"]]:
                        raw = row[name].strip()
                        if not raw:
                            continue
                        date_array = array if field["date_array"] == "same" else int(field["date_array"])
                        date_col = f"{field['date_field']}-{instance}.{date_array}"
                        date = date_value(row[date_col]) if date_col in header else None
                        if not date:
                            exclusions["missing_or_invalid_date"] += 1
                        events.append({"person": source_id, "field_id": field["field_id"],
                                       "kind": field["kind"], "vocabulary": field.get("source_vocabulary", ""),
                                       "code": raw if field["kind"] == "coded" else field["field_id"],
                                       "normalization": field.get("code_normalization", "exact_casefold"),
                                       "date": date, "value_number": raw if field["kind"] == "numeric" else "",
                                       "unit_source": "",
                                       "unit_concept_id": field.get("unit_concept_id", 0),
                                       "local_targets": field.get("target_concept_ids", []),
                                       "fallback_domain": field.get("fallback_domain", "Measurement"),
                                       "type_concept_id": field.get("type_concept_id", 0)})
        else:
            columns = spec["columns"]
            needed = [columns[key] for key in ("field_id", "kind", "source_vocabulary", "source_code", "event_date")]
            missing = set(needed) - header
            if missing:
                raise ValueError(f"Missing flat-event columns: {sorted(missing)}")
            for row in reader:
                source_id = row[person_config["id_column"]].strip()
                _add_person(people, source_id, row, person_config)
                code = row[columns["source_code"]].strip()
                if not code:
                    continue
                kind = row[columns["kind"]].strip().lower()
                if kind not in {"coded", "numeric"}:
                    raise ValueError(f"Invalid event kind: {kind}")
                date = date_value(row[columns["event_date"]])
                if not date:
                    exclusions["missing_or_invalid_date"] += 1
                unit = row.get(columns.get("unit_source", ""), "").strip()
                events.append({"person": source_id, "field_id": row[columns["field_id"]].strip(),
                               "kind": kind, "vocabulary": row[columns["source_vocabulary"]].strip(),
                               "code": code, "normalization": spec.get("code_normalization", "exact_casefold"),
                               "date": date, "value_number": row.get(columns.get("value_number", ""), "").strip(),
                               "unit_source": unit,
                               "unit_concept_id": spec.get("unit_map", {}).get(unit, 0),
                               "local_targets": [], "fallback_domain": spec.get("fallback_domain", "Observation"),
                               "type_concept_id": spec.get("type_concept_id", 0)})
    return people, events, dict(exclusions)


def review_key(event):
    return (event["field_id"], event["vocabulary"], event["code"].upper())


def diagnosis_scope(codes=(), prefixes=()):
    exact = sorted({normalize(code, "icd10_dots") for code in codes if code.strip()})
    families = sorted({normalize(prefix.strip().rstrip("*"), "icd10_dots") for prefix in prefixes if prefix.strip()})
    if any(not value for value in exact + families):
        raise ValueError("Diagnosis code or prefix cannot be empty")
    return {"codes": exact, "prefixes": families}


def select_diagnoses(events, codes=(), prefixes=()):
    """Limit ICD10 coded events; retain people and every non-diagnosis event."""
    scope = diagnosis_scope(codes, prefixes)
    exact, families = scope["codes"], scope["prefixes"]
    if not exact and not families:
        return events, scope, 0
    kept, removed, matched = [], 0, 0
    for event in events:
        if event["kind"] == "coded" and event["vocabulary"] == "ICD10":
            normalized = normalize(event["code"], "icd10_dots")
            if normalized in exact or any(normalized.startswith(prefix) for prefix in families):
                kept.append(event)
                matched += 1
            else:
                removed += 1
        else:
            kept.append(event)
    if not matched:
        raise ValueError("No ICD10 diagnosis events match the selected code or prefix")
    return kept, scope, removed


def _valid_concept(row):
    start = row["valid_start_date"].replace("-", "")
    end = row["valid_end_date"].replace("-", "")
    return (not row["invalid_reason"] and start <= TODAY <= end)


def vocabulary_candidates(vocabulary, events):
    """Exact source lookup, then Maps to / Maps to value over one Athena release."""
    wanted = collections.defaultdict(set)
    for event in events:
        if event["vocabulary"]:
            key = (event["vocabulary"], normalize(event["code"], event["normalization"]))
            wanted[key].add(review_key(event))
    source_ids, source_meta = collections.defaultdict(set), {}
    review_sources = collections.defaultdict(set)
    path = vocabulary / "CONCEPT.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if not _valid_concept(row):
                continue
            for mode in ("exact_casefold", "icd10_dots"):
                key = (row["vocabulary_id"], normalize(row["concept_code"], mode))
                for review in wanted.get(key, ()):
                    source_ids[int(row["concept_id"])].add(review)
                    review_sources[review].add(int(row["concept_id"]))
                    source_meta[int(row["concept_id"])] = row
    targets, values = collections.defaultdict(set), collections.defaultdict(set)
    if source_ids:
        with (vocabulary / "CONCEPT_RELATIONSHIP.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            header = {name: index for index, name in enumerate(next(reader))}
            source_col, target_col = header["concept_id_1"], header["concept_id_2"]
            relationship_col, invalid_col = header["relationship_id"], header["invalid_reason"]
            start_col, end_col = header["valid_start_date"], header["valid_end_date"]
            for row in reader:
                relationship = row[relationship_col]
                if (row[invalid_col] or relationship not in {"Maps to", "Maps to value"}
                        or not row[start_col].replace("-", "") <= TODAY <= row[end_col].replace("-", "")):
                    continue
                source_id = int(row[source_col])
                if source_id not in source_ids:
                    continue
                target = int(row[target_col])
                destination = targets if relationship == "Maps to" else values
                for key in source_ids[source_id]:
                    destination[key].add(target)
    target_ids = set().union(*targets.values(), *values.values()) if (targets or values) else set()
    metadata = {}
    if target_ids:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream, delimiter="\t"):
                value = int(row["concept_id"])
                if value in target_ids and _valid_concept(row) and row["standard_concept"] == "S":
                    metadata[value] = row
    result = {}
    for key in wanted.values():
        for review in key:
            matched_sources = sorted(review_sources.get(review, ()))
            proposed = sorted(target for target in targets.get(review, set()) if target in metadata)
            # A source concept already standard may map to itself when no explicit relationship exists.
            if not proposed:
                proposed = [source_id for source_id in matched_sources
                            if source_meta[source_id]["standard_concept"] == "S"]
            result[review] = {
                "source_ids": matched_sources,
                "targets": [metadata[target] if target in metadata else source_meta[target]
                            for target in proposed],
                "value_ids": sorted(target for target in values.get(review, set()) if target in metadata),
            }
    return result, metadata


def _fresh_output(path):
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def inspect(source, spec, vocabulary, output, diagnosis_codes=(), diagnosis_prefixes=()):
    _fresh_output(output)
    people, events, _ = extract(source, spec)
    events, selection, excluded_by_selection = select_diagnoses(events, diagnosis_codes, diagnosis_prefixes)
    exclusions = {"missing_or_invalid_date": sum(not event["date"] for event in events)}
    candidates, _ = vocabulary_candidates(vocabulary, events)
    grouped = collections.Counter(review_key(e) for e in events if not e["local_targets"])
    dated = collections.Counter(review_key(e) for e in events if not e["local_targets"] and e["date"])
    with (output / "mapping_review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for key, count in grouped.most_common():
            candidate = candidates.get(key, {})
            options = candidate.get("targets", [])
            writer.writerow({"field_id": key[0], "source_vocabulary": key[1], "source_code": key[2],
                             "records": count, "dated_records": dated[key],
                             "source_concept_ids": ";".join(map(str, candidate.get("source_ids", []))),
                             "candidate_targets_json": json.dumps([
                                 {"concept_id": int(row["concept_id"]), "name": row["concept_name"],
                                  "domain": row["domain_id"]} for row in options], ensure_ascii=False),
                             "candidate_value_ids": ";".join(map(str, candidate.get("value_ids", []))),
                             "decision": "needs_review", "approved_target_ids": "",
                             "approved_value_concept_id": "", "mapping_kind": "", "evidence": ""})
    candidate_keys = {key for key in grouped if candidates.get(key, {}).get("targets")}
    report = {"spec": spec["name"], "spec_sha256": spec_digest(spec),
              "source": str(source), "source_sha256": source_digest(source),
              "vocabulary_release": vocabulary_release(vocabulary), "people": len(people),
              "diagnosis_selection": selection,
              "diagnosis_events_excluded_by_selection": excluded_by_selection,
              "source_events": len(events), "reviewable_records": sum(grouped.values()),
              "dated_reviewable_records": sum(dated.values()), "distinct_review_keys": len(grouped),
              "keys_with_standard_candidates": len(candidate_keys),
              "dated_records_with_candidates": sum(dated[key] for key in candidate_keys),
              "candidate_domains": dict(collections.Counter(
                  target["domain_id"] for key in candidate_keys
                  for target in candidates[key]["targets"])),
              "excluded_or_incomplete": exclusions, "automatically_approved": 0,
              "review_file": str(output / "mapping_review.csv")}
    (output / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def read_review(path, candidates):
    approved, seen = {}, set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(REVIEW_COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("Review CSV lacks required columns")
        for row in reader:
            key = (row["field_id"].strip(), row["source_vocabulary"].strip(), row["source_code"].strip().upper())
            if key in seen or not all(key):
                raise ValueError(f"Duplicate or incomplete review key: {key}")
            seen.add(key)
            decision = row["decision"].strip().lower()
            if decision not in {"approved", "needs_review", "unmapped"}:
                raise ValueError(f"Invalid review decision for {key}: {decision}")
            if decision != "approved":
                if row["approved_target_ids"].strip() or row["approved_value_concept_id"].strip():
                    raise ValueError(f"Unapproved code has approved target: {key}")
                continue
            try:
                ids = [int(value) for value in row["approved_target_ids"].split(";")]
                value_id = int(row["approved_value_concept_id"]) if row["approved_value_concept_id"].strip() else 0
            except ValueError as error:
                raise ValueError(f"Invalid approved concept ID for {key}") from error
            if not ids or len(ids) != len(set(ids)) or any(value <= 0 for value in ids):
                raise ValueError(f"Approved targets must be distinct positive IDs for {key}")
            kind, evidence = row["mapping_kind"].strip(), row["evidence"].strip()
            if kind not in {"vocabulary_maps_to", "local_reviewed"} or not evidence:
                raise ValueError(f"Mapping kind and evidence required for {key}")
            if kind == "vocabulary_maps_to":
                candidate = candidates.get(key, {})
                allowed = {int(target["concept_id"]) for target in candidate.get("targets", [])}
                if not set(ids).issubset(allowed) or (value_id and value_id not in candidate.get("value_ids", [])):
                    raise ValueError(f"Approved vocabulary IDs not in Athena candidates for {key}")
            approved[key] = {"targets": ids, "value_id": value_id, "kind": kind, "evidence": evidence}
    return approved


def verify_review_inventory(review, events):
    """Require one unchanged count row for every reviewable source key."""
    expected = collections.Counter(review_key(event) for event in events if not event["local_targets"])
    dated = collections.Counter(review_key(event) for event in events
                                if not event["local_targets"] and event["date"])
    observed = {}
    with review.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = (row["field_id"].strip(), row["source_vocabulary"].strip(),
                   row["source_code"].strip().upper())
            observed[key] = (int(row["records"]), int(row["dated_records"]))
    if set(observed) != set(expected) or any(observed[key] != (count, dated[key])
                                              for key, count in expected.items()):
        raise ValueError("Review rows or record counts do not match the selected source events")


def target_metadata(vocabulary, ids):
    found = {}
    if not ids:
        return found
    with (vocabulary / "CONCEPT.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            concept = int(row["concept_id"])
            if concept in ids:
                found[concept] = row
            if len(found) == len(ids):
                break
    for concept in ids:
        row = found.get(concept)
        if not row or not _valid_concept(row) or row["standard_concept"] != "S":
            raise ValueError(f"Target {concept} is not a current standard concept")
    return found


def _append_event(tables, person_id, event, target, metadata, value_id=0, source_id=0):
    domain = metadata[target]["domain_id"] if target else event["fallback_domain"]
    if domain not in DOMAINS:
        raise ValueError(f"Domain {domain} is not supported for source {review_key(event)}")
    table, _, _ = DOMAINS[domain]
    record_id = len(tables[table]) + 1
    base = [record_id, person_id, target, event["date"], event["type_concept_id"]]
    if domain in {"Measurement", "Observation"}:
        base += [event["value_number"], value_id, event["unit_concept_id"], event["code"], source_id]
    else:
        base += [event["code"], source_id]
    tables[table].append(base)


def transform(people, events, approved, candidates, metadata, spec):
    tables = {name: [] for name in HEADERS}
    source_ids = sorted(people, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))
    person_ids = {source: index for index, source in enumerate(source_ids, 1)}
    event_dates = collections.defaultdict(list)
    mapped = collections.Counter()
    mapped_by_key = collections.Counter()
    exclusions = collections.Counter()
    unmapped_units = collections.Counter()
    rows_by_field_domain = collections.Counter()
    mapped_rows_by_field_domain = collections.Counter()
    mapped_rows_by_key_domain = collections.Counter()
    for source in source_ids:
        gender, birth = people[source]
        tables["person"].append([person_ids[source], gender, birth, 0, 0, source])
    for event in events:
        if not event["date"]:
            exclusions["missing_or_invalid_date"] += 1
            continue
        if event["kind"] == "numeric" and numeric_value(event["value_number"]) is None:
            exclusions["invalid_numeric_value"] += 1
            continue
        if event["unit_source"] and not event["unit_concept_id"]:
            unmapped_units[(event["field_id"], event["unit_source"])] += 1
        key = review_key(event)
        entry = approved.get(key)
        target_ids = event["local_targets"] if event["local_targets"] else (entry["targets"] if entry else [0])
        value_id = entry["value_id"] if entry else 0
        matched_sources = candidates.get(key, {}).get("source_ids", [])
        source_concept_id = matched_sources[0] if len(matched_sources) == 1 else 0
        for target in target_ids:
            domain = metadata[target]["domain_id"] if target else event["fallback_domain"]
            _append_event(tables, person_ids[event["person"]], event, target, metadata,
                          value_id if metadata.get(target, {}).get("domain_id") in {"Measurement", "Observation"} else 0,
                          source_concept_id)
            rows_by_field_domain[(event["field_id"], domain)] += 1
            if target:
                mapped_rows_by_field_domain[(event["field_id"], domain)] += 1
                mapped_rows_by_key_domain[(key, domain)] += 1
        if any(target_ids):
            mapped[event["field_id"]] += 1
            mapped_by_key[key] += 1
        event_dates[person_ids[event["person"]]].append(event["date"])
    for person_id, dates in sorted(event_dates.items()):
        tables["observation_period"].append([len(tables["observation_period"]) + 1,
                                              person_id, min(dates), max(dates), 0])
    report = {"spec": spec["name"], "table_rows": {name: len(rows) for name, rows in tables.items()},
              "input_events": len(events), "dated_events": sum(bool(event["date"]) for event in events),
              "source_events_by_field": dict(collections.Counter(event["field_id"] for event in events)),
              "dated_events_by_field": dict(collections.Counter(event["field_id"] for event in events if event["date"])),
              "mapped_source_events_by_field": dict(mapped), "excluded_events": dict(exclusions),
              "mapped_source_events_by_key": [
                  {"field_id": key[0], "source_vocabulary": key[1], "source_code": key[2], "records": count}
                  for key, count in sorted(mapped_by_key.items())
              ],
              "output_rows_by_field_domain": [
                  {"field_id": field, "domain": domain, "rows": count}
                  for (field, domain), count in sorted(rows_by_field_domain.items())
              ],
              "mapped_rows_by_field_domain": [
                  {"field_id": field, "domain": domain, "rows": count}
                  for (field, domain), count in sorted(mapped_rows_by_field_domain.items())
              ],
              "mapped_rows_by_key_domain": [
                  {"field_id": key[0], "source_vocabulary": key[1], "source_code": key[2],
                   "domain": domain, "rows": count}
                  for (key, domain), count in sorted(mapped_rows_by_key_domain.items())
              ],
              "unmapped_units": [{"field_id": field, "unit": unit, "records": count}
                                 for (field, unit), count in sorted(unmapped_units.items())],
              "mapping_note": "Approved multi-target mappings may create multiple OMOP rows; observation periods are technical event spans."}
    return tables, report


def write_tables(output, tables, report):
    _fresh_output(output)
    for table, rows in tables.items():
        with (output / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS[table])
            writer.writerows(rows)
    (output / "mapping_report.json").write_text(json.dumps(report, indent=2) + "\n")


def audit(output, expected):
    checks = {}
    person_ids = set()
    for table, wanted in expected.items():
        with (output / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            actual = list(reader)
        ids = [row[0] for row in actual]
        checks[table] = {"expected_rows": len(wanted), "actual_rows": len(actual),
                         "exact_replay": header == HEADERS[table] and actual == [[str(value) for value in row] for row in wanted],
                         "unique_ids": len(ids) == len(set(ids))}
        if table == "person":
            person_ids = set(ids)
        else:
            checks[table]["orphan_rows"] = sum(len(row) < 2 or row[1] not in person_ids for row in actual)
    return {"pass": all(check["exact_replay"] and check["unique_ids"] and not check.get("orphan_rows", 0)
                        for check in checks.values()), "tables": checks}


def run_apply(source, spec, vocabulary, review, output, check_only=False,
              diagnosis_codes=(), diagnosis_prefixes=()):
    selection = diagnosis_scope(diagnosis_codes, diagnosis_prefixes)
    preflight_path = review.parent / "preflight.json"
    if not preflight_path.exists():
        raise ValueError(f"Review file needs its matching preflight.json: {review.parent}")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    expected_identity = {"spec_sha256": spec_digest(spec), "source_sha256": source_digest(source),
                         "vocabulary_release": vocabulary_release(vocabulary),
                         "diagnosis_selection": selection}
    if any(preflight.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("Review preflight does not match this source, specification, and Athena release")
    people, events, _ = extract(source, spec)
    events, _, excluded_by_selection = select_diagnoses(events, diagnosis_codes, diagnosis_prefixes)
    candidates, _ = vocabulary_candidates(vocabulary, events)
    approved = read_review(review, candidates)
    verify_review_inventory(review, events)
    present_keys = {review_key(event) for event in events}
    if set(approved) - present_keys:
        raise ValueError("Approved mapping contains codes absent from the source")
    ids = {target for entry in approved.values() for target in entry["targets"]}
    ids.update(entry["value_id"] for entry in approved.values() if entry["value_id"])
    ids.update(target for event in events for target in event["local_targets"])
    ids.update(event["unit_concept_id"] for event in events if event["unit_concept_id"])
    ids.update(gender for gender, _ in people.values() if gender)
    ids.update(event["type_concept_id"] for event in events if event["type_concept_id"])
    metadata = target_metadata(vocabulary, ids)
    for gender, _ in people.values():
        if gender and metadata[gender]["domain_id"] != "Gender":
            raise ValueError(f"Configured gender concept {gender} has wrong domain")
    for event in events:
        for target in event["local_targets"]:
            if metadata[target]["domain_id"] != "Measurement":
                raise ValueError(f"Configured numeric target {target} is not Measurement")
        if event["unit_concept_id"] and metadata[event["unit_concept_id"]]["domain_id"] != "Unit":
            raise ValueError("Configured unit has wrong domain")
        if event["type_concept_id"] and metadata[event["type_concept_id"]]["domain_id"] != "Type Concept":
            raise ValueError("Configured type concept has wrong domain")
    for key, entry in approved.items():
        if entry["value_id"] and not any(metadata[target]["domain_id"] in {"Measurement", "Observation"}
                                         for target in entry["targets"]):
            raise ValueError(f"Maps to value for {key} has no Measurement/Observation target")
    tables, report = transform(people, events, approved, candidates, metadata, spec)
    report.update(expected_identity)
    report["review_file"] = str(review)
    report["review_sha256"] = source_digest(review)
    report["diagnosis_events_excluded_by_selection"] = excluded_by_selection
    if check_only:
        return audit(output, tables)
    write_tables(output, tables, report)
    qc = audit(output, tables)
    (output / "qc.json").write_text(json.dumps(qc, indent=2) + "\n")
    if not qc["pass"]:
        raise ValueError("Output failed replay QC")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "apply", "qc"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--input", type=Path, required=True)
        sub.add_argument("--spec", type=Path, required=True)
        sub.add_argument("--vocabulary", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--diagnosis-code", action="append", default=[],
                         help="Keep this exact ICD10 code; repeat for multiple codes")
        sub.add_argument("--diagnosis-prefix", action="append", default=[],
                         help="Keep this ICD10 code family, e.g. E11; repeat for multiple families")
        if command in {"apply", "qc"}:
            sub.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        spec = read_spec(args.spec)
        if args.command == "inspect":
            result = inspect(args.input, spec, args.vocabulary, args.output,
                             args.diagnosis_code, args.diagnosis_prefix)
        else:
            result = run_apply(args.input, spec, args.vocabulary, args.review, args.output,
                               check_only=args.command == "qc",
                               diagnosis_codes=args.diagnosis_code,
                               diagnosis_prefixes=args.diagnosis_prefix)
        print(json.dumps(result, indent=2))
        if args.command == "qc" and not result["pass"]:
            parser.exit(1)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
