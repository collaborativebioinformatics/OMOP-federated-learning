from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import nvflare.client as flare

from model import MortalityMLP
from training import auroc, train_epochs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-2)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    with np.load(args.shard) as shard:
        X_train, y_train, X_test, y_test = (shard[k] for k in ("X_train", "y_train", "X_test", "y_test"))

    model = MortalityMLP(X_train.shape[1])
    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        global_auroc = auroc(model, X_test, y_test)
        steps = train_epochs(
            model,
            X_train,
            y_train,
            args.epochs,
            lr=args.lr,
            weight_decay=args.weight_decay,
            batch_size=args.batch_size,
        )
        flare.send(
            flare.FLModel(
                params={k: v.cpu() for k, v in model.state_dict().items()},
                metrics={"auroc": global_auroc},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


if __name__ == "__main__":
    main()
