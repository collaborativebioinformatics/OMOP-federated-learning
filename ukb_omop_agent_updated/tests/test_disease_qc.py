import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


UKB_SCRIPTS = Path(__file__).resolve().parents[1] / "ukb"
sys.path.insert(0, str(UKB_SCRIPTS))
module_spec = importlib.util.spec_from_file_location("disease_qc", UKB_SCRIPTS / "disease_qc.py")
disease_qc = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(disease_qc)


class DiseaseQcTests(unittest.TestCase):
    def test_diagnosis_families_are_distinct(self):
        self.assertEqual(disease_qc.disease_for_code("G30.9"), ["AD"])
        self.assertEqual(disease_qc.disease_for_code("F00.1"), ["AD"])
        self.assertEqual(disease_qc.disease_for_code("G20"), ["PD"])
        self.assertEqual(disease_qc.disease_for_code("E11.9"), ["T2D"])
        self.assertEqual(disease_qc.disease_for_code("G21"), [])
        self.assertEqual(disease_qc.disease_for_code("E10"), [])

    def test_source_counts_allow_overlapping_disease_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ukb.tsv"
            with source.open("w", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t")
                writer.writerow(["EID", "30740-0.0", "30740-1.0", "30760-0.0"])
                writer.writerow(["a", "5.1", "5.2", "1.4"])
                writer.writerow(["b", "6.0", "", "1.2"])
            counts = disease_qc.source_lab_counts(source, {
                "AD": {"a"}, "PD": {"b"}, "T2D": {"a", "b"}})
            self.assertEqual(counts[("AD", "30740")], 2)
            self.assertEqual(counts[("PD", "30740")], 1)
            self.assertEqual(counts[("T2D", "30740")], 3)
            self.assertEqual(counts[("T2D", "30760")], 2)

    def test_quantiles(self):
        self.assertEqual(disease_qc.quantile([1, 2, 3, 4], .25), 1.75)
        self.assertEqual(disease_qc.quantile([1, 2, 3, 4], .75), 3.25)


if __name__ == "__main__":
    unittest.main()
