from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from fl_model import RiskNet
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

import omopflare as of

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"
OUT = HERE / "federated_timing.json"

SPEC = of.FeatureSpec(
    features=(
        of.Feature("resp_rate", 3024171, "measurement", unit_concept_id=8541, plausible_range=(4.0, 60.0)),
        of.Feature("heart_rate", 3027018, "measurement", unit_concept_id=8483, plausible_range=(20.0, 220.0)),
        of.Feature("spo2", 40762499, "measurement", unit_concept_id=8554, plausible_range=(50.0, 100.0)),
        of.Feature("diastolic", 21492240, "measurement", unit_concept_id=8876, plausible_range=(20.0, 150.0)),
        of.Feature("systolic", 21492239, "measurement", unit_concept_id=8876, plausible_range=(40.0, 250.0)),
        of.Feature("potassium", 3023103, "measurement", unit_concept_id=9557, plausible_range=(1.5, 9.0)),
    ),
    vocabulary_version="mimic-iv-demo-omop-0.9",
    lookback_days=3650,
    missing_indicators=True,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Time a real NVFlare job over a large OMOP cohort.")
    parser.add_argument("--cdm", type=Path, required=True)
    parser.add_argument("--sites", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()

    source = of.OmopSource(args.cdm)
    people, rows = source.count("person"), source.count("measurement")
    SPEC.to_json(SPEC_PATH)

    names = [f"site_{i}" for i in range(args.sites)]
    recipe = FedAvgRecipe(
        name="scale_federated",
        model=RiskNet(SPEC.width),
        min_clients=args.sites,
        num_rounds=args.rounds,
        train_script=str(HERE / "fl_client.py"),
    )
    set_per_site_config(
        recipe,
        {
            name: {
                "train_args": f"--cdm {args.cdm.resolve()} --spec {SPEC_PATH.resolve()} --site {i} --sites {args.sites}"
            }
            for i, name in enumerate(names)
        },
    )

    start = time.perf_counter()
    recipe.execute(SimEnv(clients=names, workspace_root=str((HERE / "workspace").resolve())))
    wall = time.perf_counter() - start

    result = {
        "people": people,
        "measurement_rows": rows,
        "sites": args.sites,
        "rounds": args.rounds,
        "wall_seconds": wall,
        "seconds_per_round": wall / args.rounds,
    }
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"\n{people:,} patients over {rows:,} rows, {args.sites} sites, {args.rounds} rounds: "
        f"{wall:.1f}s total, {wall / args.rounds:.1f}s per round"
    )


if __name__ == "__main__":
    main()
