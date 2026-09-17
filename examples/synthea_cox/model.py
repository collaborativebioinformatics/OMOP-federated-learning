from __future__ import annotations

import torch
from torch import nn


class CoxPHModel(nn.Module):
    """Linear log-risk model for Cox proportional hazards."""

    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.n_features = n_features  # NVFlare uses this to reconstruct the model.
        self.linear = nn.Linear(n_features, 1, bias=False)
        nn.init.zeros_(self.linear.weight)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.linear(features).squeeze(-1)
