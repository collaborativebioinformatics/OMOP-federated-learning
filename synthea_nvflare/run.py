from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from data import DEFAULT_HORIZON_DAYS, load_cohort, save_shards, save_site_shard
from visualize import export_result_tables

HERE = Path(__file__).resolve().parent
COHORT_ROOT = HERE.parent / "synthea_cohorts"


def parse_cohorts(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise argparse.ArgumentTypeError("Provide at least one cohort name.")
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("Cohort names must be unique.")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise argparse.ArgumentTypeError("Cohort names may contain only letters, numbers, '_' and '-'.")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one federated client per named Synthea cohort.")
    parser.add_argument("--cohorts", type=parse_cohorts, required=True, help="For example: cohort_1,cohort_2")
    parser.add_argument("--cohort-root", type=Path, default=COHORT_ROOT)
    parser.add_argument("--outcome", choices=("demo", "observed"), default="demo")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--demo-logit-intercept", type=float, default=-0.25)
    parser.add_argument(
        "--single-cohort-clients",
        type=int,
        default=3,
        help="Number of simulated clients when only one cohort is supplied (default: 3).",
    )
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=0.03)
    parser.add_argument("--build-only", action="store_true", help="Build client shards without starting NVFlare.")
    args = parser.parse_args()

    run_name = "__".join(args.cohorts)
    if len(args.cohorts) == 1:
        run_name = f"{run_name}__{args.single_cohort_clients}clients"
    shard_dir = HERE / "data" / "cohort_runs" / run_name / "shards"
    workspace = HERE / "workspace" / f"cohorts__{run_name}"
    if len(args.cohorts) == 1:
        name = args.cohorts[0]
        cohort_dir = args.cohort_root / name / "data" / "cohort"
        if not cohort_dir.is_dir():
            raise FileNotFoundError(f"Cohort folder not found: {cohort_dir}")
        cohort = load_cohort(
            cohort_dir,
            args.outcome,
            args.seed,
            args.horizon_days,
            args.demo_logit_intercept,
        )
        save_shards(
            cohort,
            shard_dir,
            args.single_cohort_clients,
            args.test_fraction,
            args.seed,
            args.horizon_days,
        )
        print(
            f"{name}: {len(cohort.patient_ids):,} patients, {int(cohort.events.sum()):,} events "
            f"-> {args.single_cohort_clients} simulated clients in {shard_dir}",
            flush=True,
        )

    for position, name in enumerate(args.cohorts):
        if len(args.cohorts) == 1:
            break
        cohort_dir = args.cohort_root / name / "data" / "cohort"
        if not cohort_dir.is_dir():
            raise FileNotFoundError(f"Cohort folder not found: {cohort_dir}")
        cohort = load_cohort(
            cohort_dir,
            args.outcome,
            args.seed + position,
            args.horizon_days,
            args.demo_logit_intercept,
        )
        shard = save_site_shard(
            cohort,
            shard_dir,
            name,
            args.test_fraction,
            args.seed + position,
            args.horizon_days,
        )
        print(
            f"{name}: {len(cohort.patient_ids):,} patients, {int(cohort.events.sum()):,} events "
            f"-> {shard}",
            flush=True,
        )

    if args.build_only:
        print("Client shards built; skipping NVFlare because --build-only was supplied.")
        return
    command = [
        sys.executable,
        "federate.py",
        "--shards",
        str(shard_dir),
        "--workspace",
        str(workspace),
        "--rounds",
        str(args.rounds),
        "--epochs",
        str(args.epochs),
        "--lr",
        str(args.lr),
    ]
    subprocess.run(command, cwd=HERE, check=True)
    run_dir = workspace / "synthea_cox_fedavg" / "server" / "simulate_job"
    result_dir = HERE / "results" / run_name
    predictions, _, _, rounds = export_result_tables(run_dir, shard_dir, result_dir)
    manifest = {
        "run_name": run_name,
        "cohorts": args.cohorts,
        "outcome": args.outcome,
        "horizon_days": args.horizon_days,
        "patients_in_test_predictions": len(predictions),
        "federated_rounds_reported": len(rounds),
        "result_dir": str(result_dir.relative_to(HERE)),
    }
    (result_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    latest = HERE / "results" / "latest_run.json"
    latest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Analysis-ready results: {result_dir}")
    print(f"RStudio report: open {HERE / 'visualize_predictions.Rmd'} and click Knit")


if __name__ == "__main__":
    main()
