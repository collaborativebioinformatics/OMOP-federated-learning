from pathlib import Path

import numpy as np
from nvflare.app_opt.pt.recipes import FedAvgRecipe
from nvflare.recipe import SimEnv, set_per_site_config

from model import MortalityMLP

JOB_NAME = "physionet_fedavg"
SHARDS = Path("data/shards")
WORKSPACE = Path("workspace")
ROUNDS = 20
EPOCHS = 1

shards = sorted(SHARDS.glob("*.npz"))
with np.load(shards[0]) as shard:
    n_features = shard["X_train"].shape[1]

recipe = FedAvgRecipe(
    name=JOB_NAME,
    model=MortalityMLP(n_features),
    min_clients=len(shards),
    num_rounds=ROUNDS,
    train_script=str(Path(__file__).parent / "client.py"),
    key_metric="auroc",
)
set_per_site_config(
    recipe,
    {shard.stem: {"train_args": f"--shard {shard.resolve()} --epochs {EPOCHS}"} for shard in shards},
)

run = recipe.execute(SimEnv(clients=[shard.stem for shard in shards], workspace_root=str(WORKSPACE.resolve())))
print(f"global model: {Path(run.get_result()) / 'server' / 'simulate_job' / 'app_server'}")
