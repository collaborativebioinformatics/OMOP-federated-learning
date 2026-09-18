import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ukb" / "make_ukb_subset.py"
spec = importlib.util.spec_from_file_location("ukb_subset", SCRIPT)
subset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subset)


class SubsetSelectionTests(unittest.TestCase):
    def test_custom_fields_keep_all_rows_without_diagnosis_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "people.tsv").write_text(
                "EID\t31-0.0\t34-0.0\t41270-0.0\n"
                "1\t0\t1970\t\n"
                "2\t1\t1980\tG20\n"
            )
            (source / "labs.tsv").write_text(
                "EID\t30740-0.0\n"
                "1\t5.1\n"
                "2\t6.2\n"
            )
            result = subset.make_subset(source, root / "out", 2, 0,
                                        fields=(31, 34, 30740, 41270))
            self.assertEqual(result["participants"], 2)
            self.assertEqual(result["diagnosis_prefixes"], [])
            self.assertEqual(result["selection"], "first participants in input sample")
            self.assertEqual((root / "out" / "ukb_subset.tsv").read_text().splitlines(), [
                "EID\t31-0.0\t34-0.0\t30740-0.0\t41270-0.0",
                "1\t0\t1970\t5.1\t",
                "2\t1\t1980\t6.2\tG20",
            ])

    def test_selects_exact_code_or_family_without_confusing_other_diagnoses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "fields.tsv").write_text(
                "EID\t31-0.0\t34-0.0\t53-0.0\t21001-0.0\t4080-0.0\t41270-0.0\t41280-0.0\n"
                "1\t0\t1970\t2020-01-01\t25\t120\tJ45.0\t2021-01-01\n"
                "2\t1\t1980\t2020-01-01\t26\t121\tJ459\t2021-01-02\n"
                "3\t1\t1990\t2020-01-01\t27\t122\tE119\t2021-01-03\n"
            )
            exact = subset.make_subset(source, root / "exact", 200, 20,
                                       all_matching=True, diagnosis_codes=("J45.0",))
            self.assertEqual(exact["participants"], 1)
            self.assertEqual(exact["diagnosis_codes"], ["J450"])
            self.assertTrue((root / "exact" / "ukb_subset.tsv").read_text().splitlines()[1].startswith("1\t"))
            family = subset.make_subset(source, root / "family", 200, 20,
                                        all_matching=True, diagnosis_prefixes=("j45",))
            self.assertEqual(family["participants"], 2)
            self.assertEqual(family["diagnosis_prefixes"], ["J45"])
            self.assertEqual(subset.make_subset(source, root / "e11", 200, 20,
                                                all_e11=True)["participants"], 1)


if __name__ == "__main__":
    unittest.main()
