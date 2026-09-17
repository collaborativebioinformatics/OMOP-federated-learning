from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import roc_auc_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from model import MortalityMLP


def train_epochs(
    model: nn.Module,
    X: np.ndarray,
    y: np.ndarray,
    epochs: int,
    lr: float = 1e-3,
    weight_decay: float = 1e-2,
    batch_size: int = 64,
) -> int:
    """Train in place and return the number of optimizer steps taken."""
    loader = DataLoader(
        TensorDataset(torch.from_numpy(X).float(), torch.from_numpy(y).float()),
        batch_size=batch_size,
        shuffle=True,
    )
    pos_weight = torch.tensor((y == 0).sum() / max((y == 1).sum(), 1), dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    model.train()
    for _ in range(epochs):
        for xb, yb in loader:
            optimizer.zero_grad()
            criterion(model(xb), yb).backward()
            optimizer.step()
    return epochs * len(loader)


def fit(X: np.ndarray, y: np.ndarray, epochs: int, **kwargs) -> nn.Module:
    model = MortalityMLP(X.shape[1])
    train_epochs(model, X, y, epochs, **kwargs)
    return model


def risk_scores(model: nn.Module, X: np.ndarray) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        return torch.sigmoid(model(torch.from_numpy(X).float())).numpy()


def auroc(model: nn.Module, X: np.ndarray, y: np.ndarray) -> float:
    return float(roc_auc_score(y, risk_scores(model, X))) if len(np.unique(y)) > 1 else float("nan")


def load_global_model(workspace: Path, job: str, n_features: int) -> nn.Module:
    checkpoint = torch.load(
        workspace / job / "server" / "simulate_job" / "app_server" / "best_FL_global_model.pt",
        map_location="cpu",
        weights_only=False,
    )
    model = MortalityMLP(n_features)
    model.load_state_dict(checkpoint["model"])
    return model
