from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import Index, as_table, feature_matrix
from .source import OmopSource
from .spec import FeatureSpec
from .validate import Finding


@dataclass(frozen=True, slots=True)
class FeatureLeak:
    """How much a feature's presence, rather than its value, tracks the outcome.

    Attributes:
        name: Feature name.
        observed: Patients with the feature recorded in the window.
        prevalence_observed: Outcome rate among those patients.
        prevalence_missing: Outcome rate among the rest.
        auroc: AUROC of the presence indicator alone against the outcome.
    """

    name: str
    observed: int
    prevalence_observed: float | None
    prevalence_missing: float | None
    auroc: float


def _auroc(labels: np.ndarray, scores: np.ndarray) -> float:
    positive, negative = scores[labels == 1], scores[labels == 0]
    if positive.size == 0 or negative.size == 0:
        return float("nan")
    order = np.argsort(np.concatenate([positive, negative]), kind="mergesort")
    ranks = np.empty(order.size, dtype=np.float64)
    ranks[order] = np.arange(1, order.size + 1)
    ties = np.concatenate([positive, negative])
    for value in np.unique(ties):
        same = ties == value
        ranks[same] = ranks[same].mean()
    above = ranks[: positive.size].sum() - positive.size * (positive.size + 1) / 2
    return float(above / (positive.size * negative.size))


def _single_class_probability(group: np.ndarray, rate: float) -> float:
    """Probability that a group this size is single-class by chance under the pooled outcome rate."""
    if group.size == 0:
        return 1.0
    share = rate if group[0] == 0 else 1.0 - rate
    return float((1.0 - share) ** group.size) if 0.0 < share < 1.0 else 1.0


def leakage_report(
    source: OmopSource,
    spec: FeatureSpec,
    index: Index,
    *,
    max_missingness_auroc: float = 0.65,
    alpha: float = 1e-3,
) -> tuple[list[FeatureLeak], list[Finding]]:
    """Check whether being measured, rather than what was measured, predicts the outcome.

    Whether a test was ordered is itself clinical signal, and a cohort built so that the outcome decides who gets
    measured will score well for the wrong reason.

    Args:
        source: The site to read from.
        spec: The frozen feature schema.
        index: SQL, a duckdb relation or an Arrow table with ``person_id``, ``index_date`` and ``label``.
        max_missingness_auroc: Largest tolerated AUROC for a presence indicator on its own, in either direction.
        alpha: Significance needed before calling a single-class group separation rather than a small sample.

    Returns:
        One :class:`FeatureLeak` per feature, and findings for the features that cross the threshold.

    Raises:
        ValueError: If the index has no ``label`` column.
    """
    table = as_table(source, index)
    if "label" not in table.column_names:
        raise ValueError("leakage_report needs a label column in the index")

    ids, matrix = feature_matrix(source, spec, table)
    labels = dict(zip(table.column("person_id").to_pylist(), table.column("label").to_pylist(), strict=True))
    y = np.array([labels[int(person)] for person in ids], dtype=np.float64)

    leaks, findings = [], []
    for position, feature in enumerate(spec.features):
        present = (~np.isnan(matrix[:, position])).astype(np.float64)
        observed = int(present.sum())
        seen = y[present == 1]
        unseen = y[present == 0]
        leak = FeatureLeak(
            name=feature.name,
            observed=observed,
            prevalence_observed=float(seen.mean()) if seen.size else None,
            prevalence_missing=float(unseen.mean()) if unseen.size else None,
            auroc=_auroc(y, present),
        )
        leaks.append(leak)

        rate = float(y.mean())
        separated = [g for g in (seen, unseen) if g.size and len(np.unique(g)) == 1]
        unlikely = [g for g in separated if _single_class_probability(g, rate) < alpha]
        if seen.size and unseen.size and unlikely:
            findings.append(
                Finding(
                    "error",
                    "missingness_separates",
                    f"{feature.name}: {unlikely[0].size} patients on one side of the presence split share an outcome, "
                    f"which chance does not explain, so presence decides the label",
                )
            )
        elif not np.isnan(leak.auroc) and abs(leak.auroc - 0.5) > max_missingness_auroc - 0.5:
            findings.append(
                Finding(
                    "warning",
                    "informative_missingness",
                    f"{feature.name}: presence alone scores {leak.auroc:.2f} against the outcome",
                )
            )
    return leaks, findings
