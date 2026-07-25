from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Protocol


DDI_SCHEMA_VERSION = "family-doctor-ddi.v1"
MAX_INGREDIENTS = 24
SEVERITY_ORDER = {"unknown": 0, "minor": 1, "moderate": 2, "major": 3, "contraindicated": 4}


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class ProviderReview:
    provider_id: str
    dataset_version: str
    checked_pairs: tuple[tuple[str, str], ...] = ()
    unresolved_pairs: tuple[tuple[str, str], ...] = ()
    findings: tuple[dict[str, str], ...] = ()
    complete_for_requested_pairs: bool = False
    errors: tuple[str, ...] = ()
    valid_until: str | None = None
    provider_mode: str = "unknown"
    coverage_complete: bool = False
    finding_use_allowed: bool = False
    source_url: str | None = None
    source_last_modified: str | None = None
    limitations: tuple[str, ...] = ()


class InteractionProvider(Protocol):
    def status(self) -> dict[str, Any]: ...

    def review_pairs(self, pairs: tuple[tuple[str, str], ...]) -> ProviderReview: ...


class NoInteractionProvider:
    def status(self) -> dict[str, Any]:
        return {
            "provider_id": "none",
            "configured": False,
            "coverage_complete": False,
            "reason": "licensed_or_owner-approved_ddi_dataset_not_configured",
        }

    def review_pairs(self, pairs: tuple[tuple[str, str], ...]) -> ProviderReview:
        return ProviderReview(
            provider_id="none",
            dataset_version="unavailable",
            unresolved_pairs=pairs,
            errors=("ddi_provider_not_configured",),
        )


class SqliteInteractionProvider:
    """Read-only adapter for an owner-approved interaction knowledge base.

    The database must contain `metadata(key TEXT PRIMARY KEY, value TEXT)` and
    `interactions(ingredient_a, ingredient_b, severity, evidence_level,
    mechanism, management, source_id, source_record_id)`. Absence of a row is
    meaningful only when metadata explicitly claims complete coverage for the
    requested controlled ingredients and the license/currentness gates pass.
    """

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise RuntimeError("ddi_database_unavailable")
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    @staticmethod
    def _supported(meta: dict[str, str]) -> set[str]:
        try:
            value = json.loads(meta.get("supported_ingredient_ids", "[]"))
        except json.JSONDecodeError:
            return set()
        if not isinstance(value, list):
            return set()
        return {str(item) for item in value if isinstance(item, str)}

    @staticmethod
    def _list_value(meta: dict[str, str], key: str) -> tuple[str, ...]:
        try:
            value = json.loads(meta.get(key, "[]"))
        except json.JSONDecodeError:
            return ()
        if not isinstance(value, list):
            return ()
        return tuple(str(item)[:500] for item in value if isinstance(item, str))

    @staticmethod
    def _metadata_complete(meta: dict[str, str], ingredients: set[str]) -> bool:
        try:
            valid_until = date.fromisoformat(meta.get("valid_until", ""))
        except ValueError:
            return False
        return (
            meta.get("schema_version") == DDI_SCHEMA_VERSION
            and meta.get("coverage_complete") == "true"
            and meta.get("license_status") == "owner_approved"
            and valid_until >= date.today()
            and ingredients <= SqliteInteractionProvider._supported(meta)
        )

    @staticmethod
    def _metadata_findings_allowed(meta: dict[str, str], ingredients: set[str]) -> bool:
        if (
            meta.get("schema_version") != DDI_SCHEMA_VERSION
            or meta.get("license_status") != "owner_approved"
        ):
            return False
        if meta.get("provider_mode") == "signal_only":
            return meta.get("finding_use_allowed") == "true"
        return SqliteInteractionProvider._metadata_complete(meta, ingredients)

    def status(self) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                meta = self._metadata(connection)
            return {
                "provider_id": meta.get("provider_id", "unknown"),
                "configured": True,
                "schema_version": meta.get("schema_version", "unknown"),
                "dataset_version": meta.get("dataset_version", "unknown"),
                "valid_until": meta.get("valid_until"),
                "license_status": meta.get("license_status", "unknown"),
                "coverage_complete": meta.get("coverage_complete") == "true",
                "provider_mode": meta.get("provider_mode", "complete_or_legacy"),
                "finding_use_allowed": self._metadata_findings_allowed(meta, set()),
                "source_url": meta.get("source_url"),
                "source_last_modified": meta.get("source_last_modified"),
                "literature_cutoff": meta.get("literature_cutoff"),
                "mapped_ingredient_count": meta.get("mapped_ingredient_count"),
                "unsupported_ingredient_ids": list(self._list_value(meta, "unsupported_ingredient_ids")),
                "limitations": list(self._list_value(meta, "limitations")),
            }
        except (OSError, RuntimeError, sqlite3.Error):
            return {
                "provider_id": "sqlite",
                "configured": True,
                "coverage_complete": False,
                "reason": "ddi_database_invalid_or_unavailable",
            }

    def review_pairs(self, pairs: tuple[tuple[str, str], ...]) -> ProviderReview:
        try:
            with closing(self._connect()) as connection:
                meta = self._metadata(connection)
                ingredients = {item for pair in pairs for item in pair}
                complete = self._metadata_complete(meta, ingredients)
                findings_allowed = self._metadata_findings_allowed(meta, ingredients)
                limitations = self._list_value(meta, "limitations")
                if not findings_allowed:
                    return ProviderReview(
                        provider_id=meta.get("provider_id", "sqlite"),
                        dataset_version=meta.get("dataset_version", "unknown"),
                        unresolved_pairs=pairs,
                        errors=("ddi_provider_metadata_not_approved",),
                        valid_until=meta.get("valid_until"),
                        provider_mode=meta.get("provider_mode", "unknown"),
                        coverage_complete=False,
                        finding_use_allowed=False,
                        source_url=meta.get("source_url"),
                        source_last_modified=meta.get("source_last_modified"),
                        limitations=limitations,
                    )
                findings: list[dict[str, str]] = []
                pairs_with_findings: set[tuple[str, str]] = set()
                for left, right in pairs:
                    rows = connection.execute(
                        """
                        SELECT severity, evidence_level, mechanism, management,
                               source_id, source_record_id
                        FROM interactions
                        WHERE ingredient_a = ? AND ingredient_b = ?
                        ORDER BY CASE lower(severity)
                            WHEN 'contraindicated' THEN 4
                            WHEN 'major' THEN 3
                            WHEN 'moderate' THEN 2
                            WHEN 'minor' THEN 1
                            ELSE 0
                        END DESC, source_id, source_record_id
                        LIMIT 8
                        """,
                        (left, right),
                    ).fetchall()
                    if rows:
                        pairs_with_findings.add((left, right))
                    for row in rows:
                        severity = _bounded(row["severity"], 32).casefold()
                        findings.append({
                            "kind": "drug_drug_interaction",
                            "ingredient_a": left,
                            "ingredient_b": right,
                            "severity": severity if severity in SEVERITY_ORDER else "unknown",
                            "evidence_level": _bounded(row["evidence_level"], 80),
                            "mechanism": _bounded(row["mechanism"], 1_200),
                            "management": _bounded(row["management"], 1_200),
                            "source_id": _bounded(row["source_id"], 120),
                            "source_record_id": _bounded(row["source_record_id"], 160),
                        })
                unresolved = () if complete else tuple(pair for pair in pairs if pair not in pairs_with_findings)
                return ProviderReview(
                    provider_id=meta.get("provider_id", "sqlite"),
                    dataset_version=meta.get("dataset_version", "unknown"),
                    checked_pairs=pairs,
                    unresolved_pairs=unresolved,
                    findings=tuple(findings),
                    complete_for_requested_pairs=complete,
                    valid_until=meta.get("valid_until"),
                    provider_mode=meta.get("provider_mode", "complete_or_legacy"),
                    coverage_complete=complete,
                    finding_use_allowed=True,
                    source_url=meta.get("source_url"),
                    source_last_modified=meta.get("source_last_modified"),
                    limitations=limitations,
                )
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            return ProviderReview(
                provider_id="sqlite",
                dataset_version="unavailable",
                unresolved_pairs=pairs,
                errors=(f"ddi_provider_error:{type(exc).__name__}",),
            )


class InteractionService:
    def __init__(self, allowed_ingredient_ids: Iterable[str], provider: InteractionProvider | None = None):
        self.allowed_ingredient_ids = frozenset(str(item) for item in allowed_ingredient_ids)
        self.provider = provider or NoInteractionProvider()

    @classmethod
    def from_environment(cls, allowed_ingredient_ids: Iterable[str]) -> "InteractionService":
        path = os.environ.get("FAMILY_HEALTH_DDI_SQLITE", "").strip()
        provider: InteractionProvider = SqliteInteractionProvider(Path(path)) if path else NoInteractionProvider()
        return cls(allowed_ingredient_ids, provider)

    def status(self) -> dict[str, Any]:
        result = dict(self.provider.status())
        result.update({
            "local_duplicate_ingredient_check": True,
            "max_ingredients_per_review": MAX_INGREDIENTS,
            "absence_never_implies_safety_without_complete_coverage": True,
        })
        return result

    def review(self, candidate_ingredient_id: Any, current_ingredient_ids: Any) -> dict[str, Any]:
        errors: list[str] = []
        candidate = str(candidate_ingredient_id or "").strip()
        if not candidate:
            errors.append("missing:candidate_ingredient_id")
        if not isinstance(current_ingredient_ids, list):
            current: list[str] = []
            errors.append("invalid:current_ingredient_ids")
        else:
            current = [str(item).strip() for item in current_ingredient_ids]
        all_ids = [candidate, *current] if candidate else current
        if len(all_ids) > MAX_INGREDIENTS:
            errors.append("invalid:too_many_ingredients")
        if any(not item for item in all_ids):
            errors.append("invalid:empty_ingredient_id")
        unknown = sorted({item for item in all_ids if item not in self.allowed_ingredient_ids})
        errors.extend(f"invalid:unknown_ingredient_id:{item}" for item in unknown)

        counts = Counter(all_ids)
        duplicates = tuple(sorted(item for item, count in counts.items() if count > 1 and item))
        unique = tuple(sorted(counts))
        pairs = tuple(_pair(left, right) for left, right in combinations(unique, 2))
        if errors:
            provider_review = ProviderReview(
                provider_id="not_run",
                dataset_version="not_run",
                unresolved_pairs=pairs,
                errors=tuple(errors),
            )
        elif not pairs:
            provider_review = ProviderReview(
                provider_id="not_required",
                dataset_version="single_ingredient",
                complete_for_requested_pairs=True,
            )
        else:
            provider_review = self.provider.review_pairs(pairs)

        duplicate_findings = tuple({
            "kind": "duplicate_active_ingredient",
            "ingredient_a": item,
            "ingredient_b": item,
            "severity": "major",
            "evidence_level": "exact_normalized_identity",
            "mechanism": "The same active ingredient appears more than once.",
            "management": "Do not combine or alternate until total exposure is checked by a clinician or pharmacist.",
            "source_id": "hermes_family_doctor_local_identity_check",
            "source_record_id": f"duplicate:{item}",
        } for item in duplicates)
        findings = duplicate_findings + provider_review.findings
        max_severity = max(
            (str(item.get("severity") or "unknown") for item in findings),
            key=lambda value: SEVERITY_ORDER.get(value, 0),
            default="none",
        )
        complete = not errors and provider_review.complete_for_requested_pairs
        if duplicates:
            disposition = "blocked_duplicate_ingredient"
        elif findings:
            disposition = "interaction_findings_require_review"
        elif complete:
            disposition = "requested_drug_drug_pairs_checked_no_finding"
        else:
            disposition = "coverage_incomplete"
        return {
            "valid": not errors,
            "candidate_ingredient_id": candidate or None,
            "current_ingredient_ids": current,
            "ingredient_count": len(all_ids),
            "unique_ingredient_count": len(unique),
            "pair_count": len(pairs),
            "checked_pair_count": len(provider_review.checked_pairs),
            "unresolved_pair_count": len(provider_review.unresolved_pairs),
            "duplicate_ingredient_ids": list(duplicates),
            "findings": list(findings),
            "finding_count": len(findings),
            "max_severity": max_severity,
            "complete_drug_drug_pair_check": complete,
            "overall_medication_safety_cleared": False,
            "disposition": disposition,
            "provider": {
                "provider_id": provider_review.provider_id,
                "dataset_version": provider_review.dataset_version,
                "valid_until": provider_review.valid_until,
                "provider_mode": provider_review.provider_mode,
                "coverage_complete": provider_review.coverage_complete,
                "finding_use_allowed": provider_review.finding_use_allowed,
                "source_url": provider_review.source_url,
                "source_last_modified": provider_review.source_last_modified,
            },
            "errors": list(provider_review.errors),
            "limitations": [
                "Coverage applies only to the normalized requested drug-drug pairs.",
                "Drug-food, supplement, allergy, disease, dose, timing and patient-specific risks require separate checks.",
                "No finding is meaningful only when complete_drug_drug_pair_check is true.",
                *provider_review.limitations,
            ],
        }
