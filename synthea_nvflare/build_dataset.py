from __future__ import annotations

import argparse
from pathlib import Path

from data import load_cohort, load_shard, save_shards

HERE = Path(__file__).resolve().parent
DEFAULT_COHORT = HERE.parent / "synthea_cohorts" / "cohort_1" / "data" / "cohort"
DEFAULT_OUTPUT = HERE / "data" / "shards"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build simulated NVFlare clients from the Synthea cohort.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-clients", type=int, default=3)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--outcome", choices=("demo", "observed"), default="demo")
    parser.add_argument("--horizon-days", type=int, default=5 * 365)
    parser.add_argument("--demo-logit-intercept", type=float, default=-0.25)
    args = parser.parse_args()

    cohort = load_cohort(args.cohort, args.outcome, args.seed, args.horizon_days, args.demo_logit_intercept)
    save_shards(cohort, args.output, args.n_clients, args.test_fraction, args.seed, args.horizon_days)
    print(
        f"{len(cohort.patient_ids)} patients, {int(cohort.events.sum())} events by day {args.horizon_days}, "
        f"{len(cohort.feature_names)} features -> {args.n_clients} clients"
    )
    print(f"outcome_source={cohort.outcome_source}")
    for shard_path in sorted(args.output.glob("client-*.pt")):
        shard = load_shard(shard_path)
        print(
            f"{shard_path.stem}: {len(shard['X_train'])} train "
            f"({int(shard['event_train'].sum())} events), {len(shard['X_test'])} test "
            f"({int(shard['event_test'].sum())} events)"
        )


if __name__ == "__main__":
    main()
