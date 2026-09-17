from __future__ import annotations

import math

import torch
from torch import nn
from torch.utils.data import DataLoader


def negative_partial_log_likelihood(log_risk: torch.Tensor, duration: torch.Tensor, event: torch.Tensor):
    order = torch.argsort(duration, descending=True)
    log_risk, event = log_risk[order], event[order]
    event_count = event.sum()
    if event_count == 0:
        raise ValueError("Cox loss requires at least one observed event.")
    log_risk_set = torch.logcumsumexp(log_risk, dim=0)
    return -((log_risk - log_risk_set) * event).sum() / event_count


def train_epochs(model: nn.Module, loader: DataLoader, epochs: int, lr: float, weight_decay: float) -> int:
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    model.train()
    for _ in range(epochs):
        for features, durations, events in loader:
            optimizer.zero_grad()
            loss = negative_partial_log_likelihood(model(features), durations, events)
            loss.backward()
            optimizer.step()
    return epochs * len(loader)


def predict_risk(model: nn.Module, features: torch.Tensor) -> torch.Tensor:
    model.eval()
    with torch.no_grad():
        return model(features).cpu()


def concordance_index(risk: torch.Tensor, duration: torch.Tensor, event: torch.Tensor) -> float:
    """Harrell's C-index in O(n log n), excluding equal-duration pairs."""
    risks = risk.detach().cpu().tolist()
    durations = duration.detach().cpu().tolist()
    events = event.detach().cpu().tolist()
    ranks = {value: index + 1 for index, value in enumerate(sorted(set(risks)))}
    tree = [0] * (len(ranks) + 1)

    def add(index: int) -> None:
        while index < len(tree):
            tree[index] += 1
            index += index & -index

    def count(index: int) -> int:
        total = 0
        while index:
            total += tree[index]
            index -= index & -index
        return total

    concordant = 0.0
    comparable = 0
    seen = 0
    ordered = sorted(range(len(risks)), key=lambda index: durations[index], reverse=True)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and durations[ordered[end]] == durations[ordered[start]]:
            end += 1
        for index in ordered[start:end]:
            if events[index] != 1:
                continue
            rank = ranks[risks[index]]
            lower = count(rank - 1)
            equal = count(rank) - lower
            concordant += lower + 0.5 * equal
            comparable += seen
        for index in ordered[start:end]:
            add(ranks[risks[index]])
            seen += 1
        start = end
    return concordant / comparable if comparable else float("nan")


def breslow_hazard_at_horizon(
    risk: torch.Tensor, duration: torch.Tensor, event: torch.Tensor, horizon_days: int
) -> float:
    centered = risk - risk.max()
    exp_risk = torch.exp(centered)
    hazard = 0.0
    for event_time in torch.unique(duration[(event == 1) & (duration <= horizon_days)]):
        deaths = ((duration == event_time) & (event == 1)).sum()
        risk_set = exp_risk[duration >= event_time].sum()
        hazard += float(deaths / risk_set)
    return hazard


def one_year_survival(model: nn.Module, train_loader: DataLoader, test_features: torch.Tensor, horizon_days: int):
    train_features, train_duration, train_event = next(iter(train_loader))
    train_risk = predict_risk(model, train_features)
    baseline = breslow_hazard_at_horizon(train_risk, train_duration, train_event, horizon_days)
    test_risk = predict_risk(model, test_features)
    # The baseline was calculated after centering training risk at its maximum.
    centered_test_risk = test_risk - train_risk.max()
    return torch.exp(-baseline * torch.exp(centered_test_risk))


def evaluate(model: nn.Module, train_loader: DataLoader, test_loader: DataLoader, horizon_days: int) -> dict[str, float]:
    test_features, duration, event = next(iter(test_loader))
    risk = predict_risk(model, test_features)
    survival = one_year_survival(model, train_loader, test_features, horizon_days)
    alive_at_horizon = (duration >= horizon_days).float()
    c_index = concordance_index(risk, duration, event)
    train_features, train_duration, train_event = next(iter(train_loader))
    loss = negative_partial_log_likelihood(
        predict_risk(model, train_features), train_duration, train_event
    )
    return {
        "c_index": c_index if math.isfinite(c_index) else 0.5,
        "brier_horizon": float(torch.mean((survival - alive_at_horizon) ** 2)),
        "cox_loss": float(loss),
    }
