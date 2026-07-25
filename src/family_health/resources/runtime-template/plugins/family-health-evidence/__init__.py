from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


from family_health.evidence_service import EvidenceService
from family_health.interactions import InteractionService
from family_health.course_memory import COMPLAINT_TYPES, COURSE_ACTIONS, COURSE_STATES, LINK_BASES
from family_health.query_planner import (
    CLINICAL_CONCEPTS,
    MEDICATION_INGREDIENTS,
    QueryRejected,
)
from family_health.request_router import ROUTER_RULE_VERSION, classify_request
from family_health.retriever import RetrievalError, SafeHttpTransport
from family_health.source_registry import SOURCE_ORDER
from family_health.service import FamilyHealthService
from family_health.store import HealthStore
from family_health.triage import ALL_FLAGS


TOOLSET = "family_health"
READ_OPERATIONS = (
    "get_facts",
    "get_recent_history",
    "evaluate_intake",
    "prepare_medication_review",
    "prepare_pediatric_review",
)
RECORD_UPDATE_OPERATIONS = (
    "get_facts",
    "propose_fact",
    "issue_confirmation_receipt",
    "confirm_fact",
)
FACT_CATEGORIES = frozenset({
    "allergy", "condition", "family_history", "hospitalization",
    "measurement", "medication", "surgery",
})
FACT_UPDATE_FIELDS = frozenset({
    "category", "fact_key", "value", "status", "source", "observed_at",
    "last_verified", "author", "supersedes_id",
})
FACT_SOURCES = frozenset({"owner_confirmed", "clinician_document", "patient_reported"})
FACT_AUTHORS = frozenset({"owner", "clinician"})
EXECUTABLE_SOURCE_IDS = (
    "who", "cdc", "uspstf", "rch", "pubmed", "ncbi_eutils", "pmc",
    "dailymed", "openfda", "rxnorm", "ema", "lactmed", "clinicaltrials",
)
MEDICATION_CONTEXT_FIELDS = frozenset({
    "patient_id", "use_case", "exact_product_or_ingredient",
    "formulation_or_concentration", "route", "current_dose", "frequency",
    "indication", "medicines_and_supplements", "allergies_and_reactions",
    "age", "patient_type", "pregnancy_breastfeeding_relevance",
    "renal_relevance", "hepatic_relevance", "prescriber_instruction",
    "current_weight", "weight_measured_at", "now", "uncertain_ingestion",
    "infant_or_severe_deterioration", "prescription_change_requested",
    "candidate_ingredient_id", "current_ingredient_ids",
    "interaction_identity_status", "supplements_present",
})
PEDIATRIC_CONTEXT_FIELDS = frozenset({
    "patient_id", "patient_type", "date_of_birth", "age", "guardian_authority",
    "weight_based_reasoning", "current_weight", "weight_measured_at",
    "formulation_or_concentration", "maximum_label_dose", "now",
    "pregnancy_relevant", "renal_relevant", "hepatic_relevant",
    "uncertain_ingestion", "infant_or_severe_deterioration",
    "prescription_change_requested", "medicines_and_supplements",
    "allergies_and_reactions", "use_case", "current_dose", "frequency",
})


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _session_value(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env

        value = get_session_env(name, default)
    except Exception:
        value = os.environ.get(name, default)
    return str(value or default)


def _runtime_identity(kwargs: dict[str, Any]) -> tuple[str, str]:
    platform = _session_value("HERMES_SESSION_PLATFORM", "local") or "local"
    profile = _session_value("HERMES_SESSION_PROFILE", os.environ.get("HERMES_PROFILE", "hermes-family-doctor"))
    raw_actor = _session_value("HERMES_SESSION_USER_ID")
    raw_session = _session_value("HERMES_SESSION_ID") or str(kwargs.get("session_id") or "")
    if platform not in {"local", "cli"} and not raw_actor:
        raise PermissionError("authenticated_actor_required")
    if not raw_session:
        raise PermissionError("authenticated_session_required")
    actor_seed = f"{profile}:{platform}:{raw_actor or 'operator'}"
    session_seed = f"{profile}:{platform}:{raw_session}"
    actor = "actor_" + hashlib.sha256(actor_seed.encode("utf-8")).hexdigest()[:24]
    session = "session_" + hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:24]
    return actor, session


def _db_path() -> Path:
    configured_home = os.environ.get("HERMES_HOME", "").strip()
    if not configured_home:
        raise RuntimeError("explicit_hermes_home_required")
    home = Path(configured_home).expanduser().resolve()
    return home / "private" / "health-records.sqlite3"


def _family_service() -> FamilyHealthService:
    path = _db_path()
    if not path.is_file():
        raise RuntimeError("private_health_store_unavailable")
    store = HealthStore(path)
    store.init()
    return FamilyHealthService(store)


def _evidence_service() -> EvidenceService:
    return EvidenceService(SafeHttpTransport())


def _interaction_service() -> InteractionService:
    return InteractionService.from_environment(MEDICATION_INGREDIENTS)


def _reject_extras(args: dict[str, Any], allowed: set[str]) -> None:
    if set(args) - allowed:
        raise QueryRejected("unsupported_or_private_field")


def _handle(action: Callable[[], dict[str, Any]]) -> str:
    try:
        return _json(action())
    except QueryRejected as exc:
        return _json({"success": False, "error": exc.code, "category": "query_rejected"})
    except RetrievalError as exc:
        return _json({"success": False, "error": exc.code, "category": "source_unavailable"})
    except PermissionError as exc:
        code = str(exc) if str(exc) in {"authenticated_actor_required", "authenticated_session_required"} else "authorization_denied"
        return _json({"success": False, "error": code, "category": "authorization"})
    except KeyError:
        return _json({"success": False, "error": "patient_or_record_not_found", "category": "not_found"})
    except ValueError:
        return _json({"success": False, "error": "invalid_family_health_request", "category": "validation"})
    except Exception:
        return _json({"success": False, "error": "family_health_service_unavailable", "category": "internal"})


def family_health_status(args: dict, **kwargs) -> str:
    def action():
        _reject_extras(args or {}, set())
        interaction_status = _interaction_service().status()
        return {
            "success": True,
            "service": "family_health",
            "version": "1.1.0",
            "rules_version": "engineering-prototype-0.4",
            "request_router_version": ROUTER_RULE_VERSION,
            "deterministic_emergency_precheck": True,
            "router_minimum_urgency_may_be_downgraded": False,
            "triage_flag_ids": sorted(ALL_FLAGS),
            "triage_requires_complete_screen": True,
            "source_registry_count": len(SOURCE_ORDER),
            "executable_source_ids": list(EXECUTABLE_SOURCE_IDS),
            "network_mode": "controlled_official_https_get_only",
            "raw_medical_web_enabled": False,
            "complete_interaction_engine": bool(interaction_status.get("coverage_complete")),
            "interaction_signal_source_configured": bool(
                interaction_status.get("configured")
                and interaction_status.get("finding_use_allowed")
            ),
            "interaction_engine": interaction_status,
            "model_can_self_attest_interaction_coverage": False,
            "exact_adult_otc_label_directions": True,
            "illness_course_memory": True,
            "course_link_requires_confirmation": True,
            "request_router": True,
            "confirmed_card_updates": True,
            "gradual_fact_reconfirmation": True,
            "pediatric_or_prescription_dose_calculation": False,
            "clinical_validation": "not_completed",
            "jurisdiction_policy": "location_aware_not_country_bound",
        }
    return _handle(action)


def family_health_classify_request(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"text", "patient_type"})
        result = classify_request(
            str(payload.get("text") or ""),
            patient_type=str(payload.get("patient_type") or "unknown"),
        )
        return {"success": True, **result}
    return _handle(action)


def family_health_select_patient(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"patient_id", "purpose"})
        patient_id = str(payload.get("patient_id") or "").strip()
        purpose = str(payload.get("purpose") or "medical_review").strip()
        if not patient_id or len(patient_id) > 80:
            raise PermissionError("patient_required")
        if purpose not in {"symptom_review", "medication_review", "pediatric_review", "prevention", "record_summary", "record_update", "medical_review"}:
            raise PermissionError("purpose_denied")
        actor, session = _runtime_identity(kwargs)
        operations = RECORD_UPDATE_OPERATIONS if purpose == "record_update" else READ_OPERATIONS
        token = _family_service().select_patient(
            patient_id=patient_id,
            actor=actor,
            session_id=session,
            purpose=purpose,
            operations=operations,
            ttl_seconds=900,
        )
        return {
            "success": True,
            "patient_selected": True,
            "capability": token,
            "expires_in_seconds": 900,
            "allowed_operations": list(operations),
        }
    return _handle(action)


def family_health_patient_summary(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "categories"})
        actor, session = _runtime_identity(kwargs)
        categories = payload.get("categories")
        if not isinstance(categories, list) or not 1 <= len(categories) <= 7:
            raise PermissionError("invalid_categories")
        normalized_categories = [str(item).strip() for item in categories]
        if any(item not in FACT_CATEGORIES for item in normalized_categories):
            raise PermissionError("invalid_categories")
        facts = _family_service().get_patient_summary(
            str(payload.get("capability") or ""),
            actor=actor,
            patient_id=str(payload.get("patient_id") or ""),
            session_id=session,
            categories=normalized_categories,
        )
        minimized = [
            {
                key: fact.get(key)
                for key in (
                    "category", "fact_key", "value", "status", "source",
                    "observed_at", "last_verified", "author",
                    "verification_status", "current_confirmation_required",
                    "reconfirmation_recommended", "reconfirmation_reason",
                )
            }
            for fact in facts
        ]
        reconfirmation_candidates = [
            {
                key: fact.get(key)
                for key in ("category", "fact_key", "value", "verification_status", "reconfirmation_reason")
            }
            for fact in minimized
            if fact.get("reconfirmation_recommended")
        ]
        return {
            "success": True,
            "facts": minimized,
            "fact_count": len(minimized),
            "unverified_fact_count": sum(
                fact.get("verification_status") != "verified_date_recorded" for fact in minimized
            ),
            "current_confirmation_required_count": sum(
                bool(fact.get("current_confirmation_required")) for fact in minimized
            ),
            "reconfirmation_recommended_count": len(reconfirmation_candidates),
            "reconfirmation_batch_limit": 1,
            "next_reconfirmation_candidate": reconfirmation_candidates[0] if reconfirmation_candidates else None,
            "categories": normalized_categories,
            "cross_patient_data": False,
        }
    return _handle(action)


def family_health_propose_card_update(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "update"})
        update = payload.get("update")
        if not isinstance(update, dict):
            raise PermissionError("card_update_required")
        _reject_extras(update, set(FACT_UPDATE_FIELDS))
        required = {"category", "fact_key", "value", "source", "author"}
        if not required <= set(update):
            raise PermissionError("card_update_incomplete")
        if update.get("category") not in FACT_CATEGORIES:
            raise PermissionError("card_update_category_denied")
        fact_key = str(update.get("fact_key") or "")
        if not fact_key or len(fact_key) > 100 or not all(char.isalnum() or char in "_:-" for char in fact_key):
            raise PermissionError("card_update_fact_key_invalid")
        if update.get("source") not in FACT_SOURCES or update.get("author") not in FACT_AUTHORS:
            raise PermissionError("card_update_provenance_invalid")
        if len(json.dumps(update.get("value"), ensure_ascii=False)) > 8_000:
            raise PermissionError("card_update_value_too_large")
        actor, session = _runtime_identity(kwargs)
        result = _family_service().propose_fact(
            str(payload.get("capability") or ""),
            actor=actor,
            patient_id=str(payload.get("patient_id") or ""),
            session_id=session,
            payload=dict(update),
        )
        return {"success": True, **result, "confirmation_required": True}
    return _handle(action)


def family_health_confirm_card_update(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "pending_id", "preview_hash", "confirmed"})
        if payload.get("confirmed") is not True:
            raise PermissionError("explicit_confirmation_required")
        actor, session = _runtime_identity(kwargs)
        service = _family_service()
        capability = str(payload.get("capability") or "")
        patient_id = str(payload.get("patient_id") or "")
        pending_id = str(payload.get("pending_id") or "")
        preview_hash = str(payload.get("preview_hash") or "")
        receipt = service.issue_confirmation_receipt(
            capability,
            actor=actor,
            patient_id=patient_id,
            session_id=session,
            pending_id=pending_id,
            preview_hash=preview_hash,
            ttl_seconds=60,
        )
        service.confirm_fact(
            capability,
            receipt=receipt,
            actor=actor,
            patient_id=patient_id,
            session_id=session,
            pending_id=pending_id,
            preview_hash=preview_hash,
        )
        return {
            "success": True,
            "write_applied": True,
            "state": "confirmed",
            "patient_namespace_preserved": True,
        }
    return _handle(action)


def family_health_recent_history(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "view", "window_days", "limit", "complaint_key"})
        actor, session = _runtime_identity(kwargs)
        complaint_key = payload.get("complaint_key")
        if complaint_key is not None:
            complaint_key = str(complaint_key).strip()
        history = _family_service().get_recent_health_history(
            str(payload.get("capability") or ""),
            actor=actor,
            patient_id=str(payload.get("patient_id") or ""),
            session_id=session,
            window_days=payload.get("window_days", 90),
            limit=payload.get("limit", 5),
            view=str(payload.get("view") or "all"),
            complaint_key=complaint_key,
        )
        history.pop("patient_id", None)
        return {"success": True, "cross_patient_data": False, **history}
    return _handle(action)


def family_health_evaluate_intake(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "structured_intake"})
        intake = payload.get("structured_intake")
        if not isinstance(intake, dict):
            raise PermissionError("structured_intake_required")
        actor, session = _runtime_identity(kwargs)
        result = _family_service().evaluate_intake(
            str(payload.get("capability") or "") or None,
            actor=actor,
            session_id=session,
            structured_intake=intake,
        )
        return {"success": True, **result}
    return _handle(action)


def family_health_medication_gate(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "context"})
        context = payload.get("context")
        if not isinstance(context, dict):
            raise PermissionError("medication_context_required")
        _reject_extras(context, set(MEDICATION_CONTEXT_FIELDS))
        patient_id = str(payload.get("patient_id") or "")
        inner_patient_id = str(context.get("patient_id") or "")
        if inner_patient_id and inner_patient_id != patient_id:
            raise PermissionError("medication_context_patient_mismatch")
        normalized_context = dict(context)
        normalized_context["patient_id"] = patient_id
        actor, session = _runtime_identity(kwargs)
        result = _family_service().prepare_medication_review(
            str(payload.get("capability") or ""),
            actor=actor,
            patient_id=patient_id,
            session_id=session,
            context=normalized_context,
            interaction_service=_interaction_service(),
        )
        return {"success": True, **result}
    return _handle(action)


def family_health_pediatric_gate(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"capability", "patient_id", "context"})
        context = payload.get("context")
        if not isinstance(context, dict):
            raise PermissionError("pediatric_context_required")
        _reject_extras(context, set(PEDIATRIC_CONTEXT_FIELDS))
        patient_id = str(payload.get("patient_id") or "")
        inner_patient_id = str(context.get("patient_id") or "")
        if inner_patient_id and inner_patient_id != patient_id:
            raise PermissionError("pediatric_context_patient_mismatch")
        normalized_context = dict(context)
        normalized_context["patient_id"] = patient_id
        actor, session = _runtime_identity(kwargs)
        result = _family_service().prepare_pediatric_review(
            str(payload.get("capability") or ""),
            actor=actor,
            patient_id=patient_id,
            session_id=session,
            context=normalized_context,
        )
        return {"success": True, **result}
    return _handle(action)


def medical_source_catalog(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"view", "topics"})
        view = payload.get("view", "sources")
        if view not in {"sources", "concepts", "ingredients", "all"}:
            raise QueryRejected("unknown_catalog_view")
        result: dict[str, Any] = {"success": True, "view": view}
        if view in {"sources", "all"}:
            topics = payload.get("topics")
            if topics is not None and (not isinstance(topics, list) or len(topics) > 8):
                raise QueryRejected("invalid_topics")
            result.update(_evidence_service().catalog([str(item) for item in topics] if topics else None))
        if view in {"concepts", "all"}:
            result["controlled_concept_ids"] = sorted(CLINICAL_CONCEPTS)
        if view in {"ingredients", "all"}:
            result["controlled_ingredient_ids"] = sorted(MEDICATION_INGREDIENTS)
        return result
    return _handle(action)


def medical_evidence_search(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"route", "concept_id", "ingredient_id", "population", "max_results"})
        return _evidence_service().search(payload)
    return _handle(action)


def medical_record_fetch(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"record_type", "identifier"})
        return _evidence_service().fetch_record(payload)
    return _handle(action)


def medical_official_page_fetch(args: dict, **kwargs) -> str:
    def action():
        payload = args or {}
        _reject_extras(payload, {"source_id", "url", "record_id"})
        return _evidence_service().fetch_official_page(payload)
    return _handle(action)


STATUS_SCHEMA = {
    "name": "family_health_status",
    "description": "Check Hermes Family Doctor safety/source capability without reading patient values.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
ROUTER_SCHEMA = {
    "name": "family_health_classify_request",
    "description": "Fail-safe classification before patient-data tools. A matched emergency_precheck is a minimum urgency that later model reasoning must never downgrade; no match is not proof of safety. Historical records, medication, prevention, labs and explicit general education do not enter the acute illness flow by default.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            "patient_type": {"type": "string", "enum": ["adult", "child", "unknown"]},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}
SELECT_SCHEMA = {
    "name": "family_health_select_patient",
    "description": "Select exactly one private patient namespace and issue a short-lived session-bound read/review capability.",
    "parameters": {
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "purpose": {"type": "string", "enum": ["symptom_review", "medication_review", "pediatric_review", "prevention", "record_summary", "record_update", "medical_review"]},
        },
        "required": ["patient_id", "purpose"],
        "additionalProperties": False,
    },
}
PROPOSE_UPDATE_SCHEMA = {
    "name": "family_health_propose_card_update",
    "description": "Prepare a patient-scoped card change without writing it. Show the preview and ask for explicit confirmation before using the confirm tool.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "update": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": sorted(FACT_CATEGORIES)},
                    "fact_key": {"type": "string", "minLength": 1, "maxLength": 100},
                    "value": {},
                    "status": {"type": "string", "enum": ["active", "inactive", "resolved"]},
                    "source": {"type": "string", "enum": sorted(FACT_SOURCES)},
                    "observed_at": {"type": "string"},
                    "last_verified": {"type": "string"},
                    "author": {"type": "string", "enum": sorted(FACT_AUTHORS)},
                    "supersedes_id": {"type": "string"},
                },
                "required": ["category", "fact_key", "value", "source", "author"],
                "additionalProperties": False,
            },
        },
        "required": ["capability", "patient_id", "update"],
        "additionalProperties": False,
    },
}
CONFIRM_UPDATE_SCHEMA = {
    "name": "family_health_confirm_card_update",
    "description": "Apply exactly one previously previewed patient-card change after the user explicitly confirms it.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "pending_id": {"type": "string"},
            "preview_hash": {"type": "string"},
            "confirmed": {"type": "boolean", "const": True},
        },
        "required": ["capability", "patient_id", "pending_id", "preview_hash", "confirmed"],
        "additionalProperties": False,
    },
}
SUMMARY_SCHEMA = {
    "name": "family_health_patient_summary",
    "description": "Read confirmed facts for the selected patient only. Never use a patient ID without a matching capability.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(FACT_CATEGORIES)},
                "minItems": 1,
                "maxItems": 7,
            },
        },
        "required": ["capability", "patient_id", "categories"],
        "additionalProperties": False,
    },
}
HISTORY_SCHEMA = {
    "name": "family_health_recent_history",
    "description": "Read bounded active/recent illness courses for the selected patient after urgency screening. Similarity is only a possible match; never merge without an explicit continuity basis.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "view": {"type": "string", "enum": ["active", "recent", "all"]},
            "window_days": {"type": "integer", "minimum": 1, "maximum": 365},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "complaint_key": {"type": "string", "enum": sorted(COMPLAINT_TYPES)},
        },
        "required": ["capability", "patient_id", "view", "window_days", "limit"],
        "additionalProperties": False,
    },
}
INTAKE_SCHEMA = {
    "name": "family_health_evaluate_intake",
    "description": "Run deterministic urgency before medical reasoning; emergency output survives missing context or storage errors.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "structured_intake": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "onset": {},
                    "course": {},
                    "severity": {},
                    "associated_symptoms": {"type": "array"},
                    "measurements": {"type": "object"},
                    "current_medications": {"type": "array"},
                    "allergies": {"type": "array"},
                    "attempts": {"type": "array"},
                    "course_tracking": {
                        "type": "object",
                        "description": "Use none only for the first stateless screen or an emergency. A complete authorized current problem must open, continue or resolve one course.",
                        "properties": {
                            "action": {"type": "string", "enum": sorted(COURSE_ACTIONS)},
                            "tracking_confirmed": {"type": "boolean"},
                            "complaint_key": {"type": "string", "enum": sorted(COMPLAINT_TYPES)},
                            "course_id": {"type": "string", "format": "uuid"},
                            "status": {"type": "string", "enum": sorted(COURSE_STATES)},
                            "link_basis": {"type": "string", "enum": sorted(LINK_BASES)},
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                    "red_flags": {
                        "type": "object",
                        "properties": {
                            flag: {"type": ["boolean", "null"]}
                            for flag in sorted(ALL_FLAGS)
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["red_flags", "course_tracking"],
                "additionalProperties": False,
            },
        },
        "required": ["structured_intake"],
        "additionalProperties": False,
    },
}
MEDICATION_SCHEMA = {
    "name": "family_health_medication_gate",
    "description": "Validate a typed medication use case and current patient context. Exact adult OTC label directions are allowed only when the returned permission is true.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "context": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "Optional duplicate; if present it must equal the outer patient_id."},
                    "use_case": {"type": "string", "enum": ["new_otc_self_care", "current_medication_review", "prescription_review"]},
                    "exact_product_or_ingredient": {"type": "string"},
                    "formulation_or_concentration": {"type": "string"},
                    "route": {"type": "string"},
                    "current_dose": {"type": ["string", "number"], "description": "Use not_taking when confirmed none."},
                    "frequency": {"type": ["string", "number"], "description": "Use not_taking when confirmed none."},
                    "indication": {"type": "string"},
                    "medicines_and_supplements": {"type": "array", "items": {"type": ["string", "object"]}},
                    "allergies_and_reactions": {"type": "array", "items": {"type": ["string", "object"]}},
                    "age": {"type": "number", "minimum": 0},
                    "patient_type": {"type": "string", "enum": ["adult", "child"]},
                    "pregnancy_breastfeeding_relevance": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "renal_relevance": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "hepatic_relevance": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "prescriber_instruction": {"type": "string"},
                    "current_weight": {"type": "number", "exclusiveMinimum": 0},
                    "weight_measured_at": {"type": "string"},
                    "now": {"type": "string"},
                    "uncertain_ingestion": {"type": "boolean"},
                    "infant_or_severe_deterioration": {"type": "boolean"},
                    "prescription_change_requested": {"type": "boolean"},
                    "candidate_ingredient_id": {"type": "string"},
                    "current_ingredient_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 23,
                        "description": "Controlled active-ingredient IDs for all other current medicines; preserve duplicates.",
                    },
                    "interaction_identity_status": {
                        "type": "string",
                        "enum": ["confirmed_complete", "incomplete_or_unknown"],
                    },
                    "supplements_present": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                },
                "required": [
                    "use_case", "exact_product_or_ingredient", "formulation_or_concentration",
                    "route", "current_dose", "frequency", "indication",
                    "medicines_and_supplements", "allergies_and_reactions", "age",
                    "pregnancy_breastfeeding_relevance", "renal_relevance", "hepatic_relevance",
                    "candidate_ingredient_id", "current_ingredient_ids",
                    "interaction_identity_status", "supplements_present",
                ],
                "additionalProperties": False,
            },
        },
        "required": ["capability", "patient_id", "context"],
        "additionalProperties": False,
    },
}
PEDIATRIC_SCHEMA = {
    "name": "family_health_pediatric_gate",
    "description": "Validate child namespace, guardian authority, date of birth, current measured weight/date and exact formulation before any child-specific reasoning.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {"type": "string"},
            "patient_id": {"type": "string"},
            "context": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "Optional duplicate; if present it must equal the outer patient_id."},
                    "patient_type": {"type": "string", "enum": ["child"]},
                    "date_of_birth": {"type": "string"},
                    "age": {"type": "number", "minimum": 0},
                    "guardian_authority": {"type": "boolean"},
                    "weight_based_reasoning": {"type": "boolean"},
                    "current_weight": {"type": "number", "exclusiveMinimum": 0},
                    "weight_measured_at": {"type": "string"},
                    "formulation_or_concentration": {"type": "string"},
                    "maximum_label_dose": {"type": ["string", "number"]},
                    "now": {"type": "string"},
                    "pregnancy_relevant": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "renal_relevant": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "hepatic_relevant": {"oneOf": [{"type": "boolean"}, {"const": "unknown"}]},
                    "uncertain_ingestion": {"type": "boolean"},
                    "infant_or_severe_deterioration": {"type": "boolean"},
                    "prescription_change_requested": {"type": "boolean"},
                    "medicines_and_supplements": {"type": "array"},
                    "allergies_and_reactions": {"type": "array"},
                    "use_case": {"type": "string"},
                    "current_dose": {"type": ["string", "number"]},
                    "frequency": {"type": ["string", "number"]},
                },
                "required": ["patient_type", "date_of_birth", "guardian_authority"],
                "additionalProperties": False,
            },
        },
        "required": ["capability", "patient_id", "context"],
        "additionalProperties": False,
    },
}
CATALOG_SCHEMA = {
    "name": "medical_source_catalog",
    "description": "List the 19 official source contracts or the controlled clinical/ingredient IDs allowed for no-PII lookup.",
    "parameters": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "enum": ["sources", "concepts", "ingredients", "all"]},
            "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        },
        "additionalProperties": False,
    },
}
SEARCH_SCHEMA = {
    "name": "medical_evidence_search",
    "description": "Search official APIs using controlled concept/ingredient IDs only. Never pass names, DOB, patient IDs, contacts, addresses or raw medical text.",
    "parameters": {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "enum": [
                    "guidelines", "reviews", "literature", "full_text",
                    "labels", "label_fallback", "identity", "lactation",
                    "trials", "public_health",
                ],
            },
            "concept_id": {"type": "string"},
            "ingredient_id": {"type": "string"},
            "population": {"type": "string", "enum": ["general", "adult", "child", "older_adult", "pregnancy"]},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["route"],
        "additionalProperties": False,
    },
}
RECORD_SCHEMA = {
    "name": "medical_record_fetch",
    "description": "Fetch an exact official public record by typed non-personal identifier. Directions appear only for an unambiguously permitted human OTC SPL.",
    "parameters": {
        "type": "object",
        "properties": {
            "record_type": {"type": "string", "enum": ["pubmed_pmid", "pmc_pmcid", "dailymed_setid", "rxnorm_rxcui", "clinical_trial_nct"]},
            "identifier": {"type": "string"},
        },
        "required": ["record_type", "identifier"],
        "additionalProperties": False,
    },
}
OFFICIAL_PAGE_SCHEMA = {
    "name": "medical_official_page_fetch",
    "description": "Fetch one exact allowlisted public page from WHO, USPSTF, RCH, EMA or LactMed. Never include patient data in the URL or record ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "enum": ["who", "uspstf", "rch", "ema", "lactmed"]},
            "url": {"type": "string", "format": "uri", "maxLength": 2000},
            "record_id": {"type": "string", "minLength": 1, "maxLength": 160},
        },
        "required": ["source_id", "url", "record_id"],
        "additionalProperties": False,
    },
}


TOOLS = (
    ("family_health_status", STATUS_SCHEMA, family_health_status),
    ("family_health_classify_request", ROUTER_SCHEMA, family_health_classify_request),
    ("family_health_select_patient", SELECT_SCHEMA, family_health_select_patient),
    ("family_health_patient_summary", SUMMARY_SCHEMA, family_health_patient_summary),
    ("family_health_propose_card_update", PROPOSE_UPDATE_SCHEMA, family_health_propose_card_update),
    ("family_health_confirm_card_update", CONFIRM_UPDATE_SCHEMA, family_health_confirm_card_update),
    ("family_health_recent_history", HISTORY_SCHEMA, family_health_recent_history),
    ("family_health_evaluate_intake", INTAKE_SCHEMA, family_health_evaluate_intake),
    ("family_health_medication_gate", MEDICATION_SCHEMA, family_health_medication_gate),
    ("family_health_pediatric_gate", PEDIATRIC_SCHEMA, family_health_pediatric_gate),
    ("medical_source_catalog", CATALOG_SCHEMA, medical_source_catalog),
    ("medical_evidence_search", SEARCH_SCHEMA, medical_evidence_search),
    ("medical_record_fetch", RECORD_SCHEMA, medical_record_fetch),
    ("medical_official_page_fetch", OFFICIAL_PAGE_SCHEMA, medical_official_page_fetch),
)


def _ready() -> bool:
    try:
        import family_health  # noqa: F401
    except ImportError:
        return False
    return True


def register(ctx) -> None:
    for name, schema, handler in TOOLS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=_ready,
            description=schema["description"],
            emoji="+",
        )
