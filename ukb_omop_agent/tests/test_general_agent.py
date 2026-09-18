import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "general_agent.py"
spec = importlib.util.spec_from_file_location("general_agent", MODULE)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
inventory_spec = importlib.util.spec_from_file_location("mapping_inventory", MODULE.with_name("mapping_inventory.py"))
inventory = importlib.util.module_from_spec(inventory_spec)
inventory_spec.loader.exec_module(inventory)


class GeneralAgentTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        vocabulary = root / "vocabulary"
        vocabulary.mkdir()
        (vocabulary / "VOCABULARY.csv").write_text("vocabulary_id\tvocabulary_name\tvocabulary_reference\tvocabulary_version\tvocabulary_concept_id\n"
                                                    "None\tOMOP Standardized Vocabularies\tOMOP generated\ttest_release\t1\n")
        concepts = [
            (1, "Family history ICD", "Observation", "ICD10", "", "Z80.0"),
            (2, "Family history finding", "Observation", "SNOMED", "S", "4167217"),
            (3, "Cancer disorder", "Condition", "SNOMED", "S", "363346000"),
            (4, "Body mass index", "Measurement", "LOINC", "S", "39156-5"),
            (5, "kg per square metre", "Unit", "UCUM", "S", "kg/m2"),
            (6, "Female", "Gender", "Gender", "S", "F"),
            (7, "Standard algorithm", "Type Concept", "Type Concept", "S", "OMOP4976953"),
        ]
        with (vocabulary / "CONCEPT.csv").open("w", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(["concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id",
                             "standard_concept", "concept_code", "valid_start_date", "valid_end_date", "invalid_reason"])
            for concept_id, name, domain, vocab, standard, code in concepts:
                writer.writerow([concept_id, name, domain, vocab, "", standard, code, "20200101", "20991231", ""])
        with (vocabulary / "CONCEPT_RELATIONSHIP.csv").open("w", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(["concept_id_1", "concept_id_2", "relationship_id", "valid_start_date", "valid_end_date", "invalid_reason"])
            writer.writerows([[1, 2, "Maps to", "20200101", "20991231", ""],
                              [1, 3, "Maps to value", "20200101", "20991231", ""],
                              [4, 4, "Maps to", "20200101", "20991231", ""]])
        source = root / "events.csv"
        source.write_text("patient_id,sex,birth_year,source_field,event_kind,vocabulary,code,date,value_number,unit\n"
                          "p1,F,1970,history,coded,ICD10,Z80.0,2020-01-01,,\n"
                          "p1,F,1970,bmi,numeric,LOINC,39156-5,2020-01-02,27.5,kg/m2\n")
        config = {
            "schema_version": 1, "name": "flat_test", "adapter": "flat_events", "delimiter": ",",
            "person": {"id_column": "patient_id", "sex_column": "sex", "birth_year_column": "birth_year",
                       "sex_map": {"F": 6}},
            "columns": {"field_id": "source_field", "kind": "event_kind",
                        "source_vocabulary": "vocabulary", "source_code": "code",
                        "event_date": "date", "value_number": "value_number", "unit_source": "unit"},
            "code_normalization": "exact_casefold", "fallback_domain": "Observation",
            "unit_map": {"kg/m2": 5},
            "observation_period": {"method": "selected_event_span", "type_concept_id": 7,
                                   "evidence": "Fixture event span"},
        }
        return source, config, vocabulary

    def test_flat_source_across_domains_with_maps_to_value_and_qc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, config, vocabulary = self.fixture(directory)
            inspection = agent.inspect(source, config, vocabulary, root / "review")
            self.assertEqual(inspection["candidate_domains"], {"Observation": 1, "Measurement": 1})
            review = root / "review" / "mapping_review.csv"
            with review.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                row["decision"] = "approved"
                row["mapping_kind"] = "vocabulary_maps_to"
                row["evidence"] = "Fixture Athena relationship"
                row["approved_target_ids"] = "2" if row["source_code"] == "Z80.0" else "4"
                if row["source_code"] == "Z80.0":
                    row["approved_value_concept_id"] = "3"
            with review.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=agent.REVIEW_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            output = root / "omop"
            report = agent.run_apply(source, config, vocabulary, review, output)
            self.assertEqual(report["mapped_source_events_by_field"], {"history": 1, "bmi": 1})
            with (output / "observation.csv").open(newline="") as stream:
                observation = list(csv.DictReader(stream))[0]
            self.assertEqual(observation["observation_concept_id"], "2")
            self.assertEqual(observation["value_as_concept_id"], "3")
            self.assertEqual(observation["observation_source_concept_id"], "1")
            with (output / "measurement.csv").open(newline="") as stream:
                measurement = list(csv.DictReader(stream))[0]
            self.assertEqual((measurement["measurement_concept_id"], measurement["unit_concept_id"]), ("4", "5"))
            with (output / "observation_period.csv").open(newline="") as stream:
                period = list(csv.DictReader(stream))[0]
            self.assertEqual((period["observation_period_start_date"],
                              period["observation_period_end_date"],
                              period["period_type_concept_id"]),
                             ("2020-01-01", "2020-01-02", "7"))
            self.assertEqual(report["observation_period_rule"], config["observation_period"])
            self.assertTrue(agent.run_apply(source, config, vocabulary, review, output, check_only=True)["pass"])
            entries, summary = inventory.build_inventory(root / "review", output, review)
            self.assertEqual((summary["mapped_source_records"], summary["unmapped_dated_records"]), (2, 0))
            self.assertEqual({item["source_code"]: item["omop_rows_written"] for item in entries},
                             {"Z80.0": 1, "39156-5": 1})
            with self.assertRaisesRegex(ValueError, "preflight does not match"):
                agent.run_apply(source, config, vocabulary, review, output, check_only=True,
                                diagnosis_prefixes=["Z80"])
            with (output / "measurement.csv").open("a") as stream:
                stream.write("corrupt\n")
            self.assertFalse(agent.run_apply(source, config, vocabulary, review, output, check_only=True)["pass"])
            with review.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=agent.REVIEW_COLUMNS)
                writer.writeheader()
                writer.writerow(rows[0])
            with self.assertRaisesRegex(ValueError, "Review rows or record counts"):
                agent.run_apply(source, config, vocabulary, review, output, check_only=True)

    def test_ukb_spec_extracts_fields_from_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ukb.tsv"
            source.write_text("EID\t31-0.0\t34-0.0\t53-0.0\t21001-0.0\t4080-0.0\t41270-0.0\t41280-0.0\n"
                              "1\t0\t1970\t2020-01-01\t25.0\t120\tE110\t2020-02-01\n")
            config = agent.read_spec(Path(__file__).resolve().parents[1] / "specs" / "ukb_pilot_v1.json")
            people, events, exclusions = agent.extract(source, config)
            self.assertEqual(len(people), 1)
            self.assertEqual(len(events), 3)
            self.assertEqual([event["date"] for event in events], ["2020-01-01", "2020-01-01", "2020-02-01"])
            self.assertEqual(exclusions, {})

    def test_diagnosis_selection_exact_and_prefix(self):
        events = [{"kind": "coded", "vocabulary": "ICD10", "code": code}
                  for code in ("E110", "E119", "I10")]
        events.append({"kind": "numeric", "vocabulary": "", "code": "21001"})
        selected, scope, removed = agent.select_diagnoses(events, codes=["E11.0"])
        self.assertEqual([item["code"] for item in selected], ["E110", "21001"])
        self.assertEqual((scope, removed), ({"codes": ["E110"], "prefixes": []}, 2))
        selected, scope, removed = agent.select_diagnoses(events, prefixes=["E11"])
        self.assertEqual([item["code"] for item in selected], ["E110", "E119", "21001"])
        self.assertEqual((scope, removed), ({"codes": [], "prefixes": ["E11"]}, 1))

    def test_mapping_inventory_missing_reasons(self):
        row = {"decision": "needs_review", "candidate_targets_json": "[]", "source_concept_ids": ""}
        self.assertEqual(inventory.classify(row, 0, 2)[0], "no_source_concept")
        row["source_concept_ids"] = "10"
        self.assertEqual(inventory.classify(row, 0, 2)[0], "no_standard_candidate")
        row["candidate_targets_json"] = '[{"domain":"Condition"}]'
        self.assertEqual(inventory.classify(row, 0, 2)[0], "awaiting_review")
        row["decision"] = "unmapped"
        self.assertEqual(inventory.classify(row, 0, 2)[0], "explicitly_unmapped")
        row["decision"] = "approved"
        self.assertEqual(inventory.classify(row, 0, 2)[0], "approved_not_written")
        self.assertEqual(inventory.classify(row, 1, 2)[0], "partially_mapped")


if __name__ == "__main__":
    unittest.main()
