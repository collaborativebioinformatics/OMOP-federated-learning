from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import torch
from torch import nn


class RiskMLP(nn.Module):
    """A small classifier over an omopflare design matrix.

    Args:
        n_features: Design matrix width, from ``spec.width``.
        hidden: Hidden layer sizes.
        dropout: Dropout probability between layers.
    """

    def __init__(self, n_features: int, hidden: Sequence[int] = (32,), dropout: float = 0.2) -> None:
        super().__init__()
        self.n_features = n_features
        self.hidden = list(hidden)
        self.dropout = dropout
        dims = [n_features, *hidden]
        layers: list[nn.Module] = []
        for in_dim, out_dim in pairwise(dims):
            layers += [nn.Linear(in_dim, out_dim), nn.ReLU(), nn.Dropout(dropout)]
        layers.append(nn.Linear(dims[-1], 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(torch.nan_to_num(x)).squeeze(-1)
