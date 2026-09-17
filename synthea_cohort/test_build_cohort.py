import argparse
import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from build_cohort import build


def write_csv(path, fields, records, delimiter=","):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(records)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class CohortTest(unittest.TestCase):
    def test_separate_tables_and_omop_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "source", root / "result"
            source.mkdir()
            write_csv(source / "patients.csv", ["Id", "BIRTHDATE", "DEATHDATE", "GENDER", "RACE", "ETHNICITY"], [
                {"Id": "p1", "BIRTHDATE": "1950-01-01", "DEATHDATE": "2021-06-01", "GENDER": "F", "RACE": "white", "ETHNICITY": "nonhispanic"},
            ])
            write_csv(source / "conditions.csv", ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION"], [
                {"START": "2020-01-10", "STOP": "", "PATIENT": "p1", "ENCOUNTER": "e1", "CODE": "44054006", "DESCRIPTION": "Type 2 diabetes mellitus"},
            ])
            write_csv(source / "observations.csv", ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "VALUE", "UNITS"], [
                {"DATE": "2020-01-01T00:00:00Z", "PATIENT": "p1", "ENCOUNTER": "e1", "CODE": "4548-4", "DESCRIPTION": "Hemoglobin A1c", "VALUE": "7.1", "UNITS": "%"},
                {"DATE": "2010-01-01T00:00:00Z", "PATIENT": "p1", "ENCOUNTER": "e0", "CODE": "4548-4", "DESCRIPTION": "Hemoglobin A1c", "VALUE": "6.0", "UNITS": "%"},
            ])
            concepts = root / "CONCEPT.csv"
            write_csv(concepts, ["concept_id", "concept_name", "vocabulary_id", "concept_code", "standard_concept", "invalid_reason"], [
                {"concept_id": "999001", "concept_name": "Type 2 diabetes", "vocabulary_id": "SNOMED", "concept_code": "44054006", "standard_concept": "S", "invalid_reason": ""},
                {"concept_id": "999002", "concept_name": "HbA1c", "vocabulary_id": "LOINC", "concept_code": "4548-4", "standard_concept": "S", "invalid_reason": ""},
            ])
            counts = build(argparse.Namespace(input=source, output=output, study_end=date(2022, 1, 1), window_days=365, omop_concepts=concepts))

            self.assertEqual(counts, {"patients": 1, "diagnoses": 1, "biomarkers": 1})
            self.assertEqual(read_csv(output / "diagnoses.csv")[0]["omop_condition_concept_id"], "999001")
            measurement = read_csv(output / "biomarkers.csv")[0]
            self.assertEqual(measurement["omop_measurement_concept_id"], "999002")
            self.assertEqual(measurement["days_from_index"], "-9")
            self.assertEqual(read_csv(output / "patients.csv")[0]["death_event"], "1")


if __name__ == "__main__":
    unittest.main()
