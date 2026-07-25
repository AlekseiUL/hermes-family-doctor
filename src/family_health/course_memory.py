from __future__ import annotations

import json
import uuid
from types import MappingProxyType
from typing import Any, Mapping

from .security import utc_text


COMPLAINT_TYPES = MappingProxyType({
    "respiratory_symptoms": "респираторные симптомы",
    "fever": "повышенная температура",
    "cough": "кашель",
    "sore_throat": "боль в горле",
    "ear_symptoms": "симптомы со стороны уха",
    "headache": "головная боль",
    "dizziness": "головокружение",
    "gastrointestinal_symptoms": "желудочно-кишечные симптомы",
    "vomiting": "рвота",
    "diarrhea": "диарея",
    "abdominal_pain": "боль в животе",
    "urinary_symptoms": "мочевые симптомы",
    "skin_symptoms": "кожные симптомы",
    "musculoskeletal_pain": "мышечная или суставная боль",
    "injury": "травма",
    "medication_reaction": "возможная реакция на лекарство",
    "other_current_problem": "другая текущая проблема",
})

COURSE_ACTIONS = frozenset({"none", "open_new", "continue_existing", "resolve_existing"})
COURSE_STATES = frozenset({"active", "recovering", "resolved"})
LINK_BASES = frozenset({
    "new_current_problem",
    "explicit_user_reference",
    "confirmed_after_prompt",
    "explicit_recovery",
})


class CourseTrackingError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _course_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CourseTrackingError("course_id_required")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise CourseTrackingError("course_id_invalid") from exc


def normalize_course_tracking(value: Any) -> dict[str, Any]:
    if value is None:
        return {"action": "none"}
    if not isinstance(value, Mapping):
        raise CourseTrackingError("course_tracking_must_be_object")
    allowed = {
        "action", "tracking_confirmed", "complaint_key", "course_id", "status", "link_basis",
    }
    if set(value) - allowed:
        raise CourseTrackingError("course_tracking_extra_field")

    action = value.get("action", "none")
    if action not in COURSE_ACTIONS:
        raise CourseTrackingError("course_action_invalid")
    if action == "none":
        return {"action": "none"}
    if value.get("tracking_confirmed") is not True:
        raise CourseTrackingError("tracking_confirmation_required")

    complaint_key = value.get("complaint_key")
    if complaint_key not in COMPLAINT_TYPES:
        raise CourseTrackingError("complaint_key_invalid")
    status = value.get("status")
    if status not in COURSE_STATES:
        raise CourseTrackingError("course_status_invalid")
    link_basis = value.get("link_basis")
    if link_basis not in LINK_BASES:
        raise CourseTrackingError("link_basis_invalid")

    normalized: dict[str, Any] = {
        "action": action,
        "tracking_confirmed": True,
        "complaint_key": complaint_key,
        "display_label": COMPLAINT_TYPES[complaint_key],
        "status": status,
        "link_basis": link_basis,
    }
    if action == "open_new":
        if status != "active" or link_basis != "new_current_problem" or value.get("course_id"):
            raise CourseTrackingError("new_course_contract_invalid")
        return normalized

    normalized["course_id"] = _course_id(value.get("course_id"))
    if action == "continue_existing":
        if status not in {"active", "recovering"}:
            raise CourseTrackingError("continued_course_status_invalid")
        if link_basis not in {"explicit_user_reference", "confirmed_after_prompt"}:
            raise CourseTrackingError("continuation_basis_invalid")
        return normalized

    if status != "resolved" or link_basis not in {"explicit_recovery", "confirmed_after_prompt"}:
        raise CourseTrackingError("resolution_contract_invalid")
    return normalized


def _text(value: Any, *, limit: int) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value.strip()[:limit]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:limit]


def _items(value: Any, *, count: int = 10, item_limit: int = 160) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [_text(item, limit=item_limit) for item in value[:count]]


def _measurements(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(value, key=str)[:10]:
        result[str(key)[:60]] = _text(value[key], limit=120)
    return result


def compact_course_summary(
    intake: Mapping[str, Any], urgency: Mapping[str, Any], *, recorded_at: Any = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "recorded_at": utc_text(recorded_at),
        "urgency_level": str(urgency.get("level") or "unknown")[:40],
    }
    scalar_fields = (("onset", 180), ("course", 180), ("severity", 80))
    for field, limit in scalar_fields:
        if field in intake:
            summary[field] = _text(intake.get(field), limit=limit)
    for field in ("associated_symptoms", "current_medications", "allergies", "attempts"):
        values = _items(intake.get(field))
        if values or field in intake:
            summary[field] = values
    measurements = _measurements(intake.get("measurements"))
    if measurements or "measurements" in intake:
        summary["measurements"] = measurements
    return summary
