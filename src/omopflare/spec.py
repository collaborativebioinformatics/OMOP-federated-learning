"""The frozen contract every site in a federation extracts against."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

Domain = Literal["measurement", "condition_occurrence", "drug_exposure", "observation"]

VALUE_COLUMN: dict[Domain, str | None] = {
    "measurement": "value_as_number",
    "observation": "value_as_number",
    "condition_occurrence": None,
    "drug_exposure": None,
}
CONCEPT_COLUMN: dict[Domain, str] = {
    "measurement": "measurement_concept_id",
    "observation": "observation_concept_id",
    "condition_occurrence": "condition_concept_id",
    "drug_exposure": "drug_concept_id",
}
DATE_COLUMN: dict[Domain, str] = {
    "measurement": "measurement_date",
    "observation": "observation_date",
    "condition_occurrence": "condition_start_date",
    "drug_exposure": "drug_exposure_start_date",
}


@dataclass(frozen=True, slots=True)
class Feature:
    """One column of the design matrix, pinned to a concept and a unit.

    ``unit_concept_id`` is required for numeric domains because ``value_as_number`` is meaningless without it;
    glucose in mg/dL and mmol/L differ by roughly eighteen-fold.
    ``plausible_range`` bounds are keyed on the concept and unit together, mirroring how the OHDSI Data Quality
    Dashboard keys ``plausibleValueLow`` and ``plausibleValueHigh``.
    """

    name: str
    concept_id: int
    domain: Domain = "measurement"
    unit_concept_id: int | None = None
    plausible_range: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.concept_id <= 0:
            raise ValueError(f"{self.name}: concept_id must be a positive standard concept, got {self.concept_id}")
        if VALUE_COLUMN[self.domain] is not None and self.unit_concept_id is None:
            raise ValueError(f"{self.name}: {self.domain} features need a unit_concept_id")

    @property
    def is_numeric(self) -> bool:
        return VALUE_COLUMN[self.domain] is not None


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """An ordered, versioned feature schema shared by every site.

    Position in ``features`` is the column index in the design matrix, so sites must never discover features from
    their own data; a site missing a concept contributes an all-missing column rather than a narrower matrix.
    ``vocabulary_version`` is recorded because concept IDs are deprecated and demoted between vocabulary releases.
    """

    features: tuple[Feature, ...]
    vocabulary_version: str
    lookback_days: int
    missing_indicators: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.features:
            raise ValueError("a spec needs at least one feature")
        if self.lookback_days <= 0:
            raise ValueError(f"lookback_days must be positive, got {self.lookback_days}")
        duplicates = {f.name for f in self.features if [g.name for g in self.features].count(f.name) > 1}
        if duplicates:
            raise ValueError(f"duplicate feature names: {sorted(duplicates)}")

    @property
    def column_names(self) -> tuple[str, ...]:
        names = tuple(f.name for f in self.features)
        if not self.missing_indicators:
            return names
        return names + tuple(f"{f.name}_missing" for f in self.features if f.is_numeric)

    @property
    def width(self) -> int:
        return len(self.column_names)

    def by_domain(self, domain: Domain) -> tuple[Feature, ...]:
        return tuple(f for f in self.features if f.domain == domain)

    @property
    def domains(self) -> tuple[Domain, ...]:
        return tuple(dict.fromkeys(f.domain for f in self.features))

    def to_json(self, path: str | Path) -> None:
        payload = {
            "vocabulary_version": self.vocabulary_version,
            "lookback_days": self.lookback_days,
            "missing_indicators": self.missing_indicators,
            "metadata": self.metadata,
            "features": [
                {
                    "name": f.name,
                    "concept_id": f.concept_id,
                    "domain": f.domain,
                    "unit_concept_id": f.unit_concept_id,
                    "plausible_range": list(f.plausible_range) if f.plausible_range else None,
                }
                for f in self.features
            ],
        }
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")

    @classmethod
    def from_json(cls, path: str | Path) -> Self:
        payload = json.loads(Path(path).read_text())
        features = tuple(
            Feature(
                name=f["name"],
                concept_id=f["concept_id"],
                domain=f["domain"],
                unit_concept_id=f["unit_concept_id"],
                plausible_range=tuple(f["plausible_range"]) if f["plausible_range"] else None,
            )
            for f in payload["features"]
        )
        return cls(
            features=features,
            vocabulary_version=payload["vocabulary_version"],
            lookback_days=payload["lookback_days"],
            missing_indicators=payload.get("missing_indicators", False),
            metadata=payload.get("metadata", {}),
        )


def spec_from_features(
    features: Sequence[Feature],
    vocabulary_version: str,
    lookback_days: int,
    *,
    missing_indicators: bool = False,
) -> FeatureSpec:
    return FeatureSpec(
        features=tuple(features),
        vocabulary_version=vocabulary_version,
        lookback_days=lookback_days,
        missing_indicators=missing_indicators,
    )
