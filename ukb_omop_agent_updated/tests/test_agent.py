import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


AGENT_PATH = Path(__file__).resolve().parents[1] / "agent.py"
spec = importlib.util.spec_from_file_location("ukb_agent", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class AgentTests(unittest.TestCase):
    def test_inventory_pairs_diagnosis_and_date_by_array(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.tsv"
            source.write_text("EID\t41270-0.0\t41280-0.0\t41270-0.1\t41280-0.1\n"
                              "1\tE110\t2020-01-01\tI10\t\n"
                              "2\tE110\t\t\t\n")
            counts, dated, people, missing = agent.inventory(source)
            self.assertEqual((people, missing), (2, 0))
            self.assertEqual(counts, {"E110": 2, "I10": 1})
            self.assertEqual(dated, {"E110": 1})

    def test_only_complete_reviewed_rows_are_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "review.csv"
            with review.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=agent.REVIEW_COLUMNS)
                writer.writeheader()
                writer.writerow({"source_code": "E110", "decision": "approved",
                                 "target_concept_id": 201826, "mapping_kind": "broad_rollup",
                                 "evidence": "Pilot E11 family rule"})
                writer.writerow({"source_code": "I10", "decision": "needs_review"})
            self.assertEqual(agent.read_review(review)["E110"]["targets"], [201826])
            with review.open("a") as stream:
                stream.write("I10,1,1,approved,999,exact,test\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                agent.read_review(review)

    def test_multiple_vocabulary_targets_require_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "review.csv"
            with review.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=agent.REVIEW_COLUMNS)
                writer.writeheader()
                writer.writerow({"source_code": "E110", "decision": "approved",
                                 "candidate_concept_ids": "201826;443735",
                                 "target_concept_id": "201826;443735",
                                 "mapping_kind": "vocabulary_maps_to", "evidence": "ICD10 Maps to"})
            self.assertEqual(agent.read_review(review)["E110"]["targets"], [201826, 443735])

    def test_icd10_maps_to_valid_standard_condition_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "CONCEPT.csv").write_text(
                "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\tconcept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason\n"
                "1\tType 2 diabetes ICD\tCondition\tICD10\tCode\t\tE11.0\t2020-01-01\t2099-12-31\t\n"
                "2\tType 2 diabetes SNOMED\tCondition\tSNOMED\tClinical Finding\tS\t44054006\t2020-01-01\t2099-12-31\t\n"
                "3\tNonstandard target\tCondition\tSNOMED\tClinical Finding\t\tother\t2020-01-01\t2099-12-31\t\n")
            (folder / "CONCEPT_RELATIONSHIP.csv").write_text(
                "concept_id_1\tconcept_id_2\trelationship_id\tvalid_start_date\tvalid_end_date\tinvalid_reason\n"
                "1\t2\tMaps to\t2020-01-01\t2099-12-31\t\n"
                "1\t3\tMaps to\t2020-01-01\t2099-12-31\t\n")
            self.assertEqual(agent.candidate_mappings(folder, {"E110", "E11.0"}),
                             {"E110": [(2, "Type 2 diabetes SNOMED")],
                              "E11.0": [(2, "Type 2 diabetes SNOMED")]})


if __name__ == "__main__":
    unittest.main()
