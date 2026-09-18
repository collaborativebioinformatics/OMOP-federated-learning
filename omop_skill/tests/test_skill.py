import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from profile_source import profile  # noqa: E402
from run_mapping import run  # noqa: E402
from validate_contract import check, compare, load_contract  # noqa: E402

CONTRACT_REV1 = load_contract(SKILL / "contract_rev1.yaml")


class SyntheaMappingTest(unittest.TestCase):
    def test_fixture_matches_contract_and_hand_written_etl(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            counts = run(SKILL / "mappings" / "synthea_3.3.0.yaml", HERE / "fixture_synthea", out)
            self.assertEqual(
                counts,
                {"person": 4, "observation_period": 3, "measurement": 6, "condition_occurrence": 2},
            )
            self.assertEqual(check(out, CONTRACT_REV1), [])
            self.assertEqual(compare(out, HERE / "expected_omop", CONTRACT_REV1), [])

    def test_allow_extra_accepts_route_b_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            for source in (HERE / "expected_omop").glob("*.csv"):
                (out / source.name).write_bytes(source.read_bytes())
            measurement = out / "measurement.csv"
            lines = measurement.read_text(encoding="utf-8").splitlines()
            lines = [lines[0] + ",measurement_source_concept_id"] + [line + ",0" for line in lines[1:]]
            measurement.write_text("\n".join(lines) + "\n", encoding="utf-8")
            (out / "observation.csv").write_text("observation_id,person_id\n1,1\n", encoding="utf-8")
            self.assertTrue(any("differ from contract" in p for p in check(out, CONTRACT_REV1)))
            self.assertEqual(check(out, CONTRACT_REV1, allow_extra=True), [])

    def test_profile_lists_codes(self):
        report = profile(HERE / "fixture_synthea")
        self.assertIn("## observations.csv: 8 rows", report)
        self.assertIn("39156-5", report)


if __name__ == "__main__":
    unittest.main()
