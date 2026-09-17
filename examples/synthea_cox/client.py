from __future__ import annotations

import argparse
from pathlib import Path

import nvflare.client as flare

from data import load_shard, make_loader
from model import CoxPHModel
from training import evaluate, train_epochs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=1e-3)
    args = parser.parse_args()

    shard = load_shard(args.shard)
    train_loader = make_loader(shard, "train")
    test_loader = make_loader(shard, "test")
    model = CoxPHModel(shard["X_train"].shape[1])
    last_params = None

    flare.init()
    site_name = flare.system_info()["site_name"]
    print(f"{site_name}: outcome_source={shard['outcome_source']}")
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        if flare.is_submit_model():
            if last_params is None:
                raise RuntimeError("Local model requested before training.")
            flare.send(flare.FLModel(params=last_params))
            continue

        model.load_state_dict(received.params)
        metrics_before = evaluate(model, train_loader, test_loader, shard["horizon_days"])
        if flare.is_evaluate():
            flare.send(flare.FLModel(metrics=metrics_before))
            continue

        steps = train_epochs(model, train_loader, args.epochs, args.lr, args.weight_decay)
        metrics_after = evaluate(model, train_loader, test_loader, shard["horizon_days"])
        last_params = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
        flare.send(
            flare.FLModel(
                params=last_params,
                metrics={
                    **metrics_before,
                    "c_index_after_local_training": metrics_after["c_index"],
                    "brier_horizon_after_local_training": metrics_after["brier_horizon"],
                },
                # Full-batch Cox training otherwise gives a 98-patient site and a
                # 50,000-patient site equal FedAvg weight. Report examples processed.
                meta={"NUM_STEPS_CURRENT_ROUND": steps * len(train_loader.dataset)},
            )
        )


if __name__ == "__main__":
    main()
