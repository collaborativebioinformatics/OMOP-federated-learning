import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from profile_source import profile  # noqa: E402
from run_mapping import run  # noqa: E402
from validate_contract import check, compare  # noqa: E402


class SyntheaMappingTest(unittest.TestCase):
    def test_fixture_matches_contract_and_hand_written_etl(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            counts = run(SKILL / "mappings" / "synthea_3.3.0.yaml", HERE / "fixture_synthea", out)
            self.assertEqual(
                counts,
                {"person": 4, "observation_period": 3, "measurement": 6, "condition_occurrence": 2},
            )
            self.assertEqual(check(out), [])
            self.assertEqual(compare(out, HERE / "expected_omop"), [])

    def test_profile_lists_codes(self):
        report = profile(HERE / "fixture_synthea")
        self.assertIn("## observations.csv: 8 rows", report)
        self.assertIn("39156-5", report)


if __name__ == "__main__":
    unittest.main()
