from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .source import OmopSource
from .spec import CONCEPT_COLUMN, VALUE_COLUMN, FeatureSpec

Level = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.check}: {self.detail}"


def validate(
    source: OmopSource,
    spec: FeatureSpec,
    *,
    max_unmapped: float = 0.05,
    strict: bool = False,
) -> list[Finding]:
    """Compare one site against the spec it is about to extract with.

    Args:
        source: The site to check.
        spec: The frozen feature schema.
        max_unmapped: Largest tolerated fraction of rows with ``concept_id = 0``.
        strict: Raise instead of returning when any finding is an error.

    Returns:
        Findings, where an ``error`` means the site's contribution would be wrong rather than noisy.

    Raises:
        ValueError: If ``strict`` and any finding is an error.
    """
    findings: list[Finding] = []
    findings += _check_vocabulary(source, spec)
    findings += _check_concepts(source, spec)
    findings += _check_unmapped(source, spec, max_unmapped)
    findings += _check_units(source, spec)
    if strict and (failures := errors(findings)):
        raise ValueError("\n".join(str(f) for f in failures))
    return findings


def _check_vocabulary(source: OmopSource, spec: FeatureSpec) -> list[Finding]:
    version = source.vocabulary_version()
    if version is None:
        return [Finding("warning", "vocabulary_version", "site ships no VOCABULARY table, version cannot be checked")]
    if version != spec.vocabulary_version:
        return [
            Finding(
                "error",
                "vocabulary_version",
                f"site is on {version!r} but the spec pins {spec.vocabulary_version!r}; "
                "concept IDs are deprecated and demoted between releases",
            )
        ]
    return []


def _check_concepts(source: OmopSource, spec: FeatureSpec) -> list[Finding]:
    if not source.has("concept"):
        return [Finding("warning", "standard_concept", "no CONCEPT table, standard-ness cannot be checked")]
    ids = ", ".join(str(f.concept_id) for f in spec.features)
    rows = source.connection.execute(
        f"select concept_id, standard_concept, invalid_reason from concept where concept_id in ({ids})"
    ).fetchall()
    known = {int(row[0]): (row[1], row[2]) for row in rows}
    findings = []
    for feature in spec.features:
        if feature.concept_id not in known:
            findings.append(
                Finding("warning", "standard_concept", f"{feature.name}: concept {feature.concept_id} not in CONCEPT")
            )
            continue
        standard, invalid_reason = known[feature.concept_id]
        if standard != "S":
            findings.append(
                Finding("error", "standard_concept", f"{feature.name}: concept {feature.concept_id} is not standard")
            )
        if invalid_reason is not None:
            findings.append(
                Finding("error", "standard_concept", f"{feature.name}: concept {feature.concept_id} is invalid")
            )
    return findings


def _check_unmapped(source: OmopSource, spec: FeatureSpec, max_unmapped: float) -> list[Finding]:
    findings = []
    for domain in spec.domains:
        if not source.has(domain):
            findings.append(Finding("warning", "missing_table", f"{domain} is absent; its features will be all-null"))
            continue
        column = CONCEPT_COLUMN[domain]
        total, unmapped = source.connection.execute(
            f"select count(*), count(*) filter (where {column} = 0) from {domain}"
        ).fetchone()
        if total and unmapped / total > max_unmapped:
            findings.append(
                Finding(
                    "error",
                    "unmapped_concepts",
                    f"{domain}: {unmapped}/{total} rows have {column} = 0 "
                    f"({unmapped / total:.1%} > {max_unmapped:.0%}); 0 means present but unmapped, not absent",
                )
            )
    return findings


def _check_units(source: OmopSource, spec: FeatureSpec) -> list[Finding]:
    findings = []
    for feature in spec.features:
        if not feature.is_numeric or not source.has(feature.domain):
            continue
        if "unit_concept_id" not in source.columns(feature.domain):
            findings.append(Finding("warning", "units", f"{feature.domain} has no unit_concept_id column"))
            continue
        rows = source.connection.execute(
            f"select unit_concept_id, count(*) from {feature.domain} "
            f"where {CONCEPT_COLUMN[feature.domain]} = {feature.concept_id} "
            f"and {VALUE_COLUMN[feature.domain]} is not null group by 1"
        ).fetchall()
        present = {int(row[0]) if row[0] is not None else None: int(row[1]) for row in rows}
        if not present:
            continue
        matching = present.get(feature.unit_concept_id, 0)
        other = sum(count for unit, count in present.items() if unit != feature.unit_concept_id)
        if matching == 0:
            findings.append(
                Finding(
                    "error",
                    "units",
                    f"{feature.name}: no rows in unit {feature.unit_concept_id}, found {sorted(present)}",
                )
            )
        elif other > matching:
            findings.append(
                Finding(
                    "warning",
                    "units",
                    f"{feature.name}: {other} rows in other units vs {matching} matching, and they are dropped",
                )
            )
    return findings


def errors(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "error"]
