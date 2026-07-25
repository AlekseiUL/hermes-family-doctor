from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .course_memory import COMPLAINT_TYPES, CourseTrackingError, normalize_course_tracking
from .documents import preview_document
from .freshness import verification_status
from .interactions import InteractionService
from .query_planner import MEDICATION_INGREDIENTS
from .security import canonical_json, digest
from .store import HealthStore
from .triage import evaluate
from .validators import validate_general_symptom, validate_medication_context, validate_pediatric_context

CONVERSATIONAL_OPERATIONS = frozenset({
    "get_facts", "get_recent_history", "evaluate_intake", "prepare_medication_review", "prepare_pediatric_review",
    "propose_fact", "issue_confirmation_receipt", "confirm_fact", "ingest_document_preview",
    "patient_audit",
})
CURRENT_CONFIRMATION_CATEGORIES = frozenset({"allergy", "medication"})


class FamilyHealthService:
    """Typed conversational boundary for the native ``family_health`` toolset.

    ``actor`` and ``session_id`` must come from the authenticated runtime adapter,
    never from model-generated text.
    """

    def __init__(self, store: HealthStore):
        self.store = store

    def select_patient(
        self,
        *,
        patient_id: str,
        actor: str,
        session_id: str,
        purpose: str,
        operations: Sequence[str],
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> str:
        requested = set(operations)
        if not requested or not requested <= CONVERSATIONAL_OPERATIONS:
            raise PermissionError("unsupported conversational operation")
        return self.store.issue_capability(
            actor=actor,
            patient_id=patient_id,
            session_id=session_id,
            purpose=purpose,
            operations=sorted(requested),
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def _authorize(
        self, capability: str, *, actor: str, patient_id: str, session_id: str, operation: str,
        now: datetime | None = None,
    ) -> None:
        self.store.validate_capability(
            capability,
            actor=actor,
            patient_id=patient_id,
            session_id=session_id,
            operation=operation,
            now=now,
        )

    def get_patient_summary(
        self, capability: str, *, actor: str, patient_id: str, session_id: str,
        categories: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        self._authorize(capability, actor=actor, patient_id=patient_id, session_id=session_id, operation="get_facts")
        facts = self.store.get_facts(patient_id)
        selected = set(categories or ())
        summary: list[dict[str, Any]] = []
        for fact in facts:
            if selected and fact["category"] not in selected:
                continue
            item = dict(fact)
            item["verification_status"] = verification_status(item.get("last_verified"))
            item["reconfirmation_recommended"] = item["verification_status"] != "verified_date_recorded"
            item["reconfirmation_reason"] = {
                "unknown": "missing_verification_date",
                "invalid": "invalid_verification_date",
            }.get(item["verification_status"])
            item["current_confirmation_required"] = (
                item["category"] in CURRENT_CONFIRMATION_CATEGORIES
                and item["verification_status"] != "verified_date_recorded"
            )
            summary.append(item)
        return summary

    def get_recent_health_history(
        self,
        capability: str,
        *,
        actor: str,
        patient_id: str,
        session_id: str,
        window_days: int = 90,
        limit: int = 5,
        view: str = "all",
        complaint_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._authorize(
            capability,
            actor=actor,
            patient_id=patient_id,
            session_id=session_id,
            operation="get_recent_history",
            now=now,
        )
        if complaint_key is not None and complaint_key not in COMPLAINT_TYPES:
            raise ValueError("unknown complaint key")
        return self.store.get_recent_history(
            patient_id,
            window_days=window_days,
            limit=limit,
            view=view,
            complaint_key=complaint_key,
            now=now,
        )

    def evaluate_intake(
        self,
        capability: str | None,
        *,
        actor: str,
        session_id: str,
        structured_intake: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        # Nothing above this line may touch identity or persistence: triage is first.
        urgency = evaluate(structured_intake.get("red_flags", {})).as_dict()
        urgency_level = urgency["level"]
        emergency = urgency_level == "emergency_now"
        result: dict[str, Any] = {
            "urgency": urgency,
            "triage_completed": True,
            "triage_coverage_complete": urgency["coverage_complete"],
            "triage_scope": "rule_based_unvalidated",
            "emergency_instruction": (
                "Немедленно позвоните в местную экстренную службу. "
                "Если номер неизвестен, попросите окружающих вызвать скорую помощь."
                if emergency else None
            ),
            "episode_persisted": False,
            "persistence_status": "not_attempted",
            "episode_id": None,
            "patient_context_authorized": False,
            "course_tracking_status": "not_attempted",
            "course_id": None,
            "course_status": None,
            "course_update_count": None,
            "encounter_kind": None,
            "next_required_action": None,
            "single_retry_allowed": False,
            "similarity_authorizes_continuation": False,
        }
        validation = validate_general_symptom(structured_intake).as_dict()
        result["validation"] = validation
        result["reasoning_allowed"] = (
            urgency_level == "no_rule_triggered"
            and urgency["coverage_complete"]
            and validation["patient_specific_allowed"]
        )
        patient_id = str(structured_intake.get("patient_id", ""))

        try:
            if not capability:
                raise PermissionError("capability required")
            self._authorize(
                capability,
                actor=actor,
                patient_id=patient_id,
                session_id=session_id,
                operation="evaluate_intake",
                now=now,
            )
        except Exception:
            result["persistence_status"] = "authorization_denied"
            result["reasoning_allowed"] = False
            return result

        result["patient_context_authorized"] = True

        if urgency_level == "insufficient_data":
            result["persistence_status"] = "blocked_triage_incomplete"
            result["reasoning_allowed"] = False
            return result

        if not validation["patient_specific_allowed"] and not emergency:
            result["persistence_status"] = "blocked_incomplete"
            return result

        raw_tracking = structured_intake.get("course_tracking")
        try:
            tracking = normalize_course_tracking(raw_tracking)
        except CourseTrackingError as exc:
            if emergency:
                tracking = {"action": "none"}
                raw_tracking = None
            else:
                result["persistence_status"] = "blocked_invalid_course_tracking"
                result["course_tracking_status"] = f"invalid:{exc.code}"
                result["next_required_action"] = "repair_course_tracking"
                result["single_retry_allowed"] = True
                return result
        if not emergency and tracking["action"] == "none":
            result["persistence_status"] = "blocked_course_tracking_required"
            result["course_tracking_status"] = "required_for_complete_current_problem"
            result["next_required_action"] = "open_new"
            result["single_retry_allowed"] = True
            return result

        try:
            saved = self.store.save_episode(
                patient_id,
                dict(structured_intake),
                urgency,
                course_tracking=dict(raw_tracking) if isinstance(raw_tracking, Mapping) else None,
                now=now,
            )
        except (KeyError, ValueError):
            result["persistence_status"] = "blocked_course_link_invalid"
            result["course_tracking_status"] = "course_link_rejected"
            result["next_required_action"] = "confirm_exact_course_or_open_new"
            result["single_retry_allowed"] = True
            return result
        except Exception:
            result["persistence_status"] = "storage_unavailable"
            if emergency:
                return result
            raise RuntimeError("episode persistence unavailable") from None
        tracking_status = {
            "initial": "opened",
            "followup": "continued",
            "resolution": "resolved",
            "standalone": "emergency_standalone",
        }[saved["encounter_kind"]]
        result.update({
            "episode_persisted": True,
            "persistence_status": "persisted",
            "episode_id": saved["episode_id"],
            "course_tracking_status": tracking_status,
            "course_id": saved["course_id"],
            "course_status": saved["course_status"],
            "course_update_count": saved["course_update_count"],
            "encounter_kind": saved["encounter_kind"],
        })
        return result

    def prepare_medication_review(
        self, capability: str, *, actor: str, patient_id: str, session_id: str,
        context: Mapping[str, Any], interaction_service: InteractionService | None = None,
    ) -> dict[str, Any]:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="prepare_medication_review",
        )
        if context.get("patient_id") != patient_id:
            raise PermissionError("medication context patient mismatch")
        engine = interaction_service or InteractionService.from_environment(MEDICATION_INGREDIENTS)
        review = engine.review(
            context.get("candidate_ingredient_id"),
            context.get("current_ingredient_ids"),
        )
        return {
            **validate_medication_context(context, interaction_review=review).as_dict(),
            "interaction_review": review,
        }

    def prepare_pediatric_review(
        self, capability: str, *, actor: str, patient_id: str, session_id: str, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="prepare_pediatric_review",
        )
        if context.get("patient_id") != patient_id:
            raise PermissionError("pediatric context patient mismatch")
        return validate_pediatric_context(context).as_dict()

    def propose_fact(
        self, capability: str, *, actor: str, patient_id: str, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._authorize(capability, actor=actor, patient_id=patient_id, session_id=session_id, operation="propose_fact")
        pending_id, payload_hash = self.store.propose_fact(patient_id, payload, requested_by=actor)
        preview = {"patient_id": patient_id, "pending_id": pending_id, "payload": payload}
        preview_hash = digest(canonical_json(preview))
        return {
            "state": "pending", "pending_id": pending_id, "payload_hash": payload_hash,
            "preview": preview, "preview_hash": preview_hash, "write_applied": False,
        }

    def issue_confirmation_receipt(
        self,
        capability: str,
        *,
        actor: str,
        patient_id: str,
        session_id: str,
        pending_id: str,
        preview_hash: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> str:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="issue_confirmation_receipt", now=now,
        )
        return self.store.issue_confirmation_receipt(
            actor=actor, patient_id=patient_id, session_id=session_id, pending_id=pending_id,
            preview_hash=preview_hash, ttl_seconds=ttl_seconds, now=now,
        )

    def confirm_fact(
        self,
        capability: str,
        *,
        receipt: str,
        actor: str,
        patient_id: str,
        session_id: str,
        pending_id: str,
        preview_hash: str,
        now: datetime | None = None,
    ) -> str:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="confirm_fact", now=now,
        )
        return self.store.confirm_fact_with_receipt(
            receipt=receipt, actor=actor, patient_id=patient_id, session_id=session_id,
            pending_id=pending_id, preview_hash=preview_hash, now=now,
        )

    def ingest_document_preview(
        self, capability: str, *, actor: str, patient_id: str, session_id: str, document: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="ingest_document_preview",
        )
        return preview_document(document).as_dict()

    def get_patient_audit(
        self, capability: str, *, actor: str, patient_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        self._authorize(
            capability, actor=actor, patient_id=patient_id, session_id=session_id,
            operation="patient_audit",
        )
        return self.store.operator_audit(patient_id)

    def status(self) -> dict[str, Any]:
        return {
            "service": "family_health",
            "rules_version": "engineering-prototype-0.4",
            "clinician_validated": False,
            "network_fetch_enabled_in_core": False,
            "runtime_adapter_version": "1.1.0",
            "illness_course_memory": True,
            "interaction_review": InteractionService.from_environment(MEDICATION_INGREDIENTS).status(),
        }
