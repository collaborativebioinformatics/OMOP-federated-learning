"""Per-site summaries that are safe to send to the server."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

MIN_CELL_COUNT = 5


@dataclass(frozen=True, slots=True)
class SiteStats:
    """Column-wise count, sum and sum of squares, which combine into a global scaler.

    These three are safe to exchange because none of them is any individual's value.
    Minima and maxima are not included for exactly that reason: each is a real patient's measurement.
    """

    n: np.ndarray
    total: np.ndarray
    total_squared: np.ndarray

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> SiteStats:
        observed = ~np.isnan(matrix)
        filled = np.where(observed, matrix, 0.0)
        return cls(
            n=observed.sum(axis=0).astype(np.int64),
            total=filled.sum(axis=0),
            total_squared=(filled**2).sum(axis=0),
        )

    def __add__(self, other: SiteStats) -> SiteStats:
        return SiteStats(
            n=self.n + other.n,
            total=self.total + other.total,
            total_squared=self.total_squared + other.total_squared,
        )

    @property
    def mean(self) -> np.ndarray:
        return np.divide(self.total, self.n, out=np.full_like(self.total, np.nan), where=self.n > 0)

    @property
    def std(self) -> np.ndarray:
        variance = (
            np.divide(self.total_squared, self.n, out=np.full_like(self.total_squared, np.nan), where=self.n > 0)
            - self.mean**2
        )
        return np.sqrt(np.clip(variance, 0.0, None))

    def suppressed(self, min_cell_count: int = MIN_CELL_COUNT) -> np.ndarray:
        """Columns backed by too few patients to release, per the site's disclosure rules."""
        return self.n < min_cell_count

    def redacted(self, min_cell_count: int = MIN_CELL_COUNT) -> SiteStats:
        keep = ~self.suppressed(min_cell_count)
        return SiteStats(
            n=np.where(keep, self.n, 0),
            total=np.where(keep, self.total, 0.0),
            total_squared=np.where(keep, self.total_squared, 0.0),
        )


def combine(stats: Iterable[SiteStats]) -> SiteStats:
    iterator = iter(stats)
    total = next(iterator)
    for item in iterator:
        total = total + item
    return total


def standardize(matrix: np.ndarray, stats: SiteStats) -> np.ndarray:
    """Centre and scale with a scaler the caller supplies, so a site never derives one from another site's data."""
    std = np.where((stats.std == 0) | np.isnan(stats.std), 1.0, stats.std)
    mean = np.where(np.isnan(stats.mean), 0.0, stats.mean)
    return (matrix - mean) / std


def prevalence(labels: Sequence[float] | np.ndarray, min_cell_count: int = MIN_CELL_COUNT) -> float | None:
    """Outcome rate, or None when too few events to report."""
    array = np.asarray(labels)
    events = int(np.nansum(array))
    if events < min_cell_count or len(array) - events < min_cell_count:
        return None
    return float(events / len(array))
