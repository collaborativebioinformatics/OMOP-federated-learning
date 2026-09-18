import csv
import sys
import tempfile
import unittest
from pathlib import Path


UKB_SCRIPTS = Path(__file__).resolve().parents[1] / "ukb"
sys.path.insert(0, str(UKB_SCRIPTS))
import visual_qc


def write_table(path, columns, records):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


class VisualQcTests(unittest.TestCase):
    def test_period_bucket_boundaries(self):
        self.assertEqual(visual_qc.period_bucket(1), "1 day")
        self.assertEqual(visual_qc.period_bucket(30), "2–30 days")
        self.assertEqual(visual_qc.period_bucket(31), "31–180 days")
        self.assertEqual(visual_qc.period_bucket(365), "181–365 days")
        self.assertEqual(visual_qc.period_bucket(366), "1–5 years")
        self.assertEqual(visual_qc.period_bucket(1826), ">5 years")

    def test_collect_reports_lab_and_period_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_table(root / "person.csv", ["person_id"], [{"person_id": "1"}])
            write_table(root / "condition_occurrence.csv",
                        ["person_id", "condition_source_value", "condition_start_date"],
                        [{"person_id": "1", "condition_source_value": "G20",
                          "condition_start_date": "2020-01-02"}])
            write_table(root / "measurement.csv",
                        ["person_id", "measurement_source_value", "measurement_date",
                         "value_as_number"],
                        [{"person_id": "1", "measurement_source_value": "30870",
                          "measurement_date": "2020-01-01", "value_as_number": "-0.5"},
                         {"person_id": "1", "measurement_source_value": "30740",
                          "measurement_date": "2020-02-01", "value_as_number": "5.1"}])
            write_table(root / "observation_period.csv",
                        ["person_id", "observation_period_start_date",
                         "observation_period_end_date", "period_type_concept_id"],
                        [{"person_id": "1", "observation_period_start_date": "2020-01-01",
                          "observation_period_end_date": "2020-01-02",
                          "period_type_concept_id": "32880"}])
            people, groups, values, group_values, negatives, durations, summary, checks = (
                visual_qc.collect(root))
            self.assertEqual(people, {"1"})
            self.assertEqual(groups["PD"], {"1"})
            self.assertEqual(values["30870"], [-0.5])
            self.assertEqual(group_values[("PD", "30870")], [-0.5])
            self.assertEqual(negatives["30870"], 1)
            self.assertEqual(durations, [("1", 2)])
            self.assertEqual(summary[0]["one_day_periods"], 0)
            self.assertEqual(checks["events_outside_period"], 1)
            self.assertEqual(checks["event_rows_checked"], 3)
            self.assertEqual(checks["persons_without_period"], 0)


if __name__ == "__main__":
    unittest.main()
