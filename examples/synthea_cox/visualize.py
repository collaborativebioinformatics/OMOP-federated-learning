from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch

from data import load_shard, make_loader, torch_load_compat
from model import CoxPHModel
from training import one_year_survival, predict_risk

HERE = Path(__file__).resolve().parent
DEFAULT_RUN = HERE / "workspace" / "synthea_cox_fedavg" / "server" / "simulate_job"


def load_round_metrics(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def metric_series(rounds: list[dict], name: str) -> tuple[list[int], list[float]]:
    x, y = [], []
    for record in rounds:
        values = {item["name"]: item["value"] for item in record["aggregated_metrics"]}
        if name in values:
            x.append(int(record["round"]))
            y.append(float(values[name]))
    return x, y


def load_global_model(path: Path, n_features: int) -> CoxPHModel:
    payload = torch_load_compat(path)
    state = payload["model"] if "model" in payload else payload
    model = CoxPHModel(n_features)
    model.load_state_dict(state)
    model.eval()
    return model


def collect_predictions(model: CoxPHModel, shard_paths: list[Path]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    feature_names: list[str] = []
    for shard_path in shard_paths:
        shard = load_shard(shard_path)
        feature_names = shard["feature_names"]
        train_loader = make_loader(shard, "train")
        test_features = shard["X_test"]
        survival = one_year_survival(model, train_loader, test_features, shard["horizon_days"])
        risk = predict_risk(model, test_features)
        for index, patient_id in enumerate(shard["patient_ids_test"]):
            rows.append(
                {
                    "client": shard_path.stem,
                    "patient_id": patient_id,
                    "duration_days": float(shard["duration_test"][index]),
                    "event": int(shard["event_test"][index]),
                    "predicted_survival": float(survival[index]),
                    "log_risk": float(risk[index]),
                    "horizon_days": int(shard["horizon_days"]),
                    "outcome_source": shard["outcome_source"],
                }
            )
    return rows, feature_names


def write_predictions(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_coefficients(path: Path, feature_names: list[str], model: CoxPHModel) -> None:
    coefficients = model.linear.weight.detach().cpu().flatten().tolist()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["feature", "coefficient", "hazard_ratio"])
        writer.writeheader()
        for feature, coefficient in zip(feature_names, coefficients):
            writer.writerow(
                {
                    "feature": feature,
                    "coefficient": coefficient,
                    "hazard_ratio": float(torch.exp(torch.tensor(coefficient))),
                }
            )


def write_round_metrics(path: Path, rounds: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["round", "metric", "value"])
        writer.writeheader()
        for record in rounds:
            for metric in record["aggregated_metrics"]:
                writer.writerow(
                    {"round": int(record["round"]), "metric": metric["name"], "value": metric["value"]}
                )


def export_result_tables(run: Path, shards: Path, output: Path) -> tuple[list[dict], list[str], CoxPHModel, list[dict]]:
    """Export model outputs in language-neutral CSV files for Python or R."""
    shard_paths = sorted(shards.glob("client-*.pt"))
    if not shard_paths:
        raise FileNotFoundError(f"No client shards found in {shards}; run run.py first.")
    metrics_path = run / "metrics" / "round_metrics.jsonl"
    model_path = run / "app_server" / "best_FL_global_model.pt"
    if not model_path.exists():
        model_path = run / "app_server" / "FL_global_model.pt"
    if not metrics_path.exists() or not model_path.exists():
        raise FileNotFoundError(f"Missing NVFlare results under {run}; run run.py first.")

    first = load_shard(shard_paths[0])
    model = load_global_model(model_path, first["X_train"].shape[1])
    predictions, feature_names = collect_predictions(model, shard_paths)
    rounds = load_round_metrics(metrics_path)
    output.mkdir(parents=True, exist_ok=True)
    write_predictions(output / "predictions.csv", predictions)
    write_coefficients(output / "coefficients.csv", feature_names, model)
    write_round_metrics(output / "round_metrics.csv", rounds)
    return predictions, feature_names, model, rounds


def plot_dashboard(
    output: Path,
    rounds: list[dict],
    predictions: list[dict],
    feature_names: list[str],
    model: CoxPHModel,
) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(17, 6), constrained_layout=True)

    ax = axes[0]
    for metric, label, color in (
        ("c_index", "C-index", "#276FBF"),
        ("brier_horizon", "Brier score", "#D1495B"),
    ):
        x, y = metric_series(rounds, metric)
        ax.plot(x, y, marker="o", markersize=3, label=label, color=color)
    ax.axhline(0.5, color="gray", linestyle="--", linewidth=1, label="C-index chance")
    ax.set(title="Federated validation by round", xlabel="Federated round", ylabel="Metric")
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.2)
    ax.legend(frameon=False)

    ax = axes[1]
    ordered = sorted(predictions, key=lambda row: row["predicted_survival"])
    for event, label, color, marker in (
        (0, "Censored/alive at horizon", "#2A9D8F", "o"),
        (1, "Event before horizon", "#D1495B", "x"),
    ):
        selected = [(i, row) for i, row in enumerate(ordered) if row["event"] == event]
        ax.scatter(
            [i for i, _ in selected],
            [row["predicted_survival"] for _, row in selected],
            label=label,
            color=color,
            marker=marker,
            alpha=0.85,
        )
    horizon_years = predictions[0]["horizon_days"] / 365
    ax.set(
        title=f"Held-out {horizon_years:g}-year predictions",
        xlabel="Patients sorted by predicted survival",
        ylabel="Predicted survival probability",
    )
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, fontsize=8)

    ax = axes[2]
    coefficients = model.linear.weight.detach().cpu().flatten().tolist()
    positions = list(range(len(feature_names)))
    colors = ["#D1495B" if value > 0 else "#2A9D8F" for value in coefficients]
    ax.barh(positions, coefficients, color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(positions, labels=feature_names)
    ax.invert_yaxis()
    ax.set(title="Global Cox coefficients", xlabel="Log-hazard coefficient")
    ax.grid(axis="x", alpha=0.2)

    figure.suptitle("Synthea federated Cox PH results", fontsize=15)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize federated Cox PH predictions and metrics.")
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN, help="NVFlare simulate_job directory")
    parser.add_argument("--shards", type=Path, default=HERE / "data" / "shards")
    parser.add_argument("--output", type=Path, default=HERE / "results")
    args = parser.parse_args()

    predictions, feature_names, model, rounds = export_result_tables(args.run, args.shards, args.output)
    csv_path = args.output / "predictions.csv"
    coefficient_path = args.output / "coefficients.csv"
    metrics_csv_path = args.output / "round_metrics.csv"
    plot_path = args.output / "prediction_dashboard.png"
    plot_dashboard(plot_path, rounds, predictions, feature_names, model)
    print(f"Wrote {len(predictions)} held-out predictions to {csv_path}")
    print(f"Wrote model coefficients to {coefficient_path}")
    print(f"Wrote round metrics to {metrics_csv_path}")
    print(f"Wrote dashboard to {plot_path}")


if __name__ == "__main__":
    main()
