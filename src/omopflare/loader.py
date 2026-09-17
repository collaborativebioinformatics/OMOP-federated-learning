from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

import numpy as np
import sparse
import torch
from scipy.sparse import csr_matrix
from torch.utils.data import BatchSampler, DataLoader, Dataset, IterableDataset, RandomSampler, SequentialSampler

from .features import Index, Layout, as_table, extract, to_matrix
from .source import OmopSource
from .spec import FeatureSpec
from .stats import SiteStats, standardize


def _to_tensor(matrix: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(matrix, dtype=np.float32))


def _to_csr(matrix: sparse.SparseArray) -> csr_matrix:
    return csr_matrix(matrix.to_scipy_sparse(), dtype=np.float32)


class CohortDataset(Dataset):
    """An in-memory cohort, for sites small enough to hold one.

    A sparse design matrix is kept sparse and densified one batch at a time, so a wide bag-of-codes spec costs
    its stored values rather than rows times features.
    Indexing takes a batch of positions, which is what :func:`dataloader` supplies.

    Args:
        features: The design matrix, dense or sparse.
        labels: One label per row.
        person_ids: Optional person IDs, kept so predictions can be joined back.

    Raises:
        ValueError: If the row counts of ``features`` and ``labels`` disagree.
    """

    def __init__(
        self,
        features: np.ndarray | sparse.SparseArray,
        labels: Sequence[float] | np.ndarray,
        person_ids: np.ndarray | None = None,
    ) -> None:
        self.sparse = isinstance(features, sparse.SparseArray)
        self.features = _to_csr(features) if self.sparse else _to_tensor(features)
        self.labels = torch.as_tensor(np.asarray(labels), dtype=torch.float32)
        self.person_ids = person_ids
        if self.features.shape[0] != len(self.labels):
            raise ValueError(f"{self.features.shape[0]} rows but {len(self.labels)} labels")

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int | Sequence[int]) -> tuple[torch.Tensor, torch.Tensor]:
        positions = torch.as_tensor(index)
        if self.sparse:
            rows = torch.from_numpy(self.features[positions.numpy()].toarray())
        else:
            rows = self.features[positions]
        return rows, self.labels[positions]


class StreamingCohort(IterableDataset):
    """A cohort read from disk one batch at a time, for sites that do not fit in memory.

    Args:
        source: The site to read from.
        spec: The frozen feature schema.
        index: Table with ``person_id``, ``index_date`` and a ``label`` column.
        scaler: Optional statistics to standardise with, usually the federation's combined scaler.
        batch_size: Rows per extraction batch.
        shuffle_buffer: Rows held back for shuffling; 0 preserves scan order.
        layout: Passed to :func:`omopflare.to_matrix`.
    """

    def __init__(
        self,
        source: OmopSource,
        spec: FeatureSpec,
        index: Index,
        *,
        scaler: SiteStats | None = None,
        batch_size: int = 50_000,
        shuffle_buffer: int = 0,
        layout: Layout = "auto",
    ) -> None:
        index = as_table(source, index)
        if "label" not in index.column_names:
            raise ValueError("index table needs a label column")
        self.source = source
        self.spec = spec
        self.index = index
        self.scaler = scaler
        self.batch_size = batch_size
        self.shuffle_buffer = shuffle_buffer
        self.layout = layout
        self._labels = dict(zip(index.column("person_id").to_pylist(), index.column("label").to_pylist(), strict=True))

    def __iter__(self) -> Iterator[tuple[torch.Tensor, torch.Tensor]]:
        buffer: list[tuple[torch.Tensor, torch.Tensor]] = []
        generator = np.random.default_rng()
        for batch in extract(self.source, self.spec, self.index, batch_size=self.batch_size):
            person_ids, matrix = to_matrix(batch, self.spec, layout=self.layout)
            if self.scaler is not None and not isinstance(matrix, sparse.SparseArray):
                matrix = standardize(matrix, self.scaler)
            features = _to_tensor(matrix)
            labels = torch.as_tensor([self._labels[int(p)] for p in person_ids], dtype=torch.float32)
            for row in range(len(person_ids)):
                buffer.append((features[row], labels[row]))
                if len(buffer) > self.shuffle_buffer:
                    position = generator.integers(len(buffer)) if self.shuffle_buffer else 0
                    yield buffer.pop(int(position))
        generator.shuffle(buffer)
        yield from buffer


def site_statistics(
    source: OmopSource,
    spec: FeatureSpec,
    index: Index,
    *,
    batch_size: int = 50_000,
) -> SiteStats:
    """Summarise a site in one streaming pass, without holding the cohort in memory.

    Args:
        source: The site to read from.
        spec: The frozen feature schema.
        index: Table with ``person_id`` and ``index_date``.
        batch_size: Rows per extraction batch.

    Returns:
        Count, sum and sum of squares per column, safe to send to the server.
    """
    index = as_table(source, index)
    total: SiteStats | None = None
    for batch in extract(source, spec, index, batch_size=batch_size):
        _, matrix = to_matrix(batch, spec, layout="dense")
        stats = SiteStats.from_matrix(matrix)
        total = stats if total is None else total + stats
    if total is None:
        raise ValueError("the index table selected no people")
    return total


def dataloader(
    dataset: Dataset | IterableDataset,
    *,
    batch_size: int = 256,
    shuffle: bool = False,
    num_workers: int = 0,
    **kwargs: Mapping[str, object],
) -> DataLoader:
    """Wrap a cohort in a ``DataLoader`` that indexes a batch at a time rather than a row at a time.

    Args:
        dataset: A :class:`CohortDataset` or :class:`StreamingCohort`.
        batch_size: Rows per training batch.
        shuffle: Shuffle between epochs; streaming cohorts shuffle through their buffer instead.
        num_workers: Worker processes.
        **kwargs: Passed through to ``DataLoader``.

    Returns:
        A ``DataLoader`` over the cohort.
    """
    if isinstance(dataset, IterableDataset):
        return DataLoader(dataset, batch_size=batch_size, num_workers=num_workers, **kwargs)
    inner = RandomSampler(dataset) if shuffle else SequentialSampler(dataset)
    sampler = BatchSampler(inner, batch_size=batch_size, drop_last=False)
    return DataLoader(dataset, sampler=sampler, batch_size=None, num_workers=num_workers, **kwargs)
