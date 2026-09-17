from __future__ import annotations

import argparse
from pathlib import Path

from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

from data import load_shard
from model import CoxPHModel

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run federated Cox PH training with NVFlare FedAvg.")
    parser.add_argument("--shards", type=Path, default=HERE / "data" / "shards")
    parser.add_argument("--workspace", type=Path, default=HERE / "workspace")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=0.03)
    args = parser.parse_args()

    shards = sorted(args.shards.glob("client-*.pt"))
    if len(shards) < 2:
        raise FileNotFoundError(f"Need at least two client shards in {args.shards}; run build_dataset.py first.")
    first = load_shard(shards[0])
    n_features = first["X_train"].shape[1]
    recipe = FedAvgRecipe(
        name="synthea_cox_fedavg",
        model=CoxPHModel(n_features),
        min_clients=len(shards),
        num_rounds=args.rounds,
        train_script=str(HERE / "client.py"),
        key_metric="c_index",
    )
    set_per_site_config(
        recipe,
        {
            shard.stem: {
                "train_args": f"--shard {shard.resolve()} --epochs {args.epochs} --lr {args.lr}"
            }
            for shard in shards
        },
    )
    run = recipe.execute(
        SimEnv(clients=[shard.stem for shard in shards], workspace_root=str(args.workspace.resolve()))
    )
    result = Path(run.get_result())
    print(f"Simulation completed: {result}")
    print(f"Global model directory: {result / 'server' / 'simulate_job' / 'app_server'}")


if __name__ == "__main__":
    main()
