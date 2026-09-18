from __future__ import annotations

import torch
from torch import nn


class RiskNet(nn.Module):
    """Classifier over an omopflare design matrix.

    Args:
        n_features: Design matrix width, from ``spec.width``.
        hidden: Hidden layer size.
    """

    def __init__(self, n_features: int, hidden: int = 16) -> None:
        super().__init__()
        self.n_features = n_features
        self.hidden = hidden
        self.net = nn.Sequential(nn.Linear(n_features, hidden), nn.ReLU(), nn.Linear(hidden, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)
