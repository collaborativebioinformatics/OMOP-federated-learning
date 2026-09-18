from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import nvflare.client as flare
from model import RiskModel
from training import auroc, cohorts, matrices, train

import omopflare as of


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--scaler", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--lr", type=float, default=5e-2)
    args = parser.parse_args()

    spec = of.FeatureSpec.from_json(args.spec)
    scaler = of.SiteStats(**dict(np.load(args.scaler)))
    train_index, test_index, source = cohorts(args.site, spec)
    train_x, train_y = matrices(source, spec, train_index, scaler)
    test_x, test_y = matrices(source, spec, test_index, scaler)
    model = RiskModel(spec.width)

    flare.init()
    while flare.is_running():
        received = flare.receive()
        if received is None:
            break
        model.load_state_dict(received.params)
        score = auroc(model, test_x, test_y)
        steps = train(model, train_x, train_y, epochs=args.epochs, lr=args.lr)
        flare.send(
            flare.FLModel(
                params={key: value.cpu() for key, value in model.state_dict().items()},
                metrics={"auroc": score},
                meta={"NUM_STEPS_CURRENT_ROUND": steps},
            )
        )


if __name__ == "__main__":
    main()
