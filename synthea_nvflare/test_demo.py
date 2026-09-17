from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch

from data import DEFAULT_HORIZON_DAYS, load_cohort, load_shard, make_loader, save_shards
from model import CoxPHModel
from training import concordance_index, evaluate, negative_partial_log_likelihood, train_epochs

HERE = Path(__file__).resolve().parent
COHORT = HERE.parent / "synthea_cohorts" / "cohort_1" / "data" / "cohort"


class DemoTest(unittest.TestCase):
    def test_observed_endpoint_rejects_zero_events(self):
        with self.assertRaisesRegex(ValueError, "zero deaths"):
            load_cohort(COHORT, "observed", horizon_days=365)

    def test_shards_and_cox_training(self):
        cohort = load_cohort(COHORT, "demo", horizon_days=DEFAULT_HORIZON_DAYS)
        self.assertEqual(len(cohort.patient_ids), 98)
        self.assertGreaterEqual(cohort.events.sum(), 35)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            save_shards(
                cohort, output, n_clients=3, test_fraction=0.25, seed=7, horizon_days=DEFAULT_HORIZON_DAYS
            )
            paths = sorted(output.glob("*.pt"))
            self.assertEqual(len(paths), 3)
            for path in paths:
                shard = load_shard(path)
                self.assertEqual(shard["outcome_source"], "simulated_1825_day_demo_outcome")
                self.assertEqual(shard["X_train"].shape[1], len(cohort.feature_names))
                self.assertFalse(torch.isnan(shard["X_train"]).any())
                self.assertGreater(shard["event_train"].sum(), 0)

            shard = load_shard(paths[0])
            train_loader, test_loader = make_loader(shard, "train"), make_loader(shard, "test")
            model = CoxPHModel(shard["X_train"].shape[1])
            before = evaluate(model, train_loader, test_loader, DEFAULT_HORIZON_DAYS)
            train_epochs(model, train_loader, epochs=2, lr=0.01, weight_decay=0.0)
            after = evaluate(model, train_loader, test_loader, DEFAULT_HORIZON_DAYS)
            self.assertTrue(0 <= before["brier_horizon"] <= 1)
            self.assertTrue(0 <= after["c_index"] <= 1)

    def test_cox_loss_is_finite(self):
        risk = torch.tensor([0.2, -0.1, 0.4])
        duration = torch.tensor([10.0, 20.0, 30.0])
        event = torch.tensor([1.0, 0.0, 1.0])
        self.assertTrue(torch.isfinite(negative_partial_log_likelihood(risk, duration, event)))

    def test_fast_concordance_handles_risk_and_time_ties(self):
        risk = torch.tensor([0.2, 0.2, 0.8, -0.1, 0.4])
        duration = torch.tensor([10.0, 20.0, 20.0, 30.0, 40.0])
        event = torch.tensor([1.0, 1.0, 1.0, 0.0, 1.0])
        concordant = 0.0
        comparable = 0
        for i in range(len(risk)):
            if event[i] != 1:
                continue
            for j in range(len(risk)):
                if duration[i] >= duration[j]:
                    continue
                comparable += 1
                concordant += float(risk[i] > risk[j]) + 0.5 * float(risk[i] == risk[j])
        self.assertAlmostEqual(concordance_index(risk, duration, event), concordant / comparable)


if __name__ == "__main__":
    unittest.main()
