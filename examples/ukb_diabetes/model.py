from __future__ import annotations

import torch
from torch import nn


class RiskModel(nn.Module):
    """Logistic regression over an omopflare feature matrix.

    Args:
        n_features: Feature matrix width, from ``spec.width``.
    """

    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features
        self.linear = nn.Linear(n_features, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.linear(torch.nan_to_num(x)).squeeze(-1)
