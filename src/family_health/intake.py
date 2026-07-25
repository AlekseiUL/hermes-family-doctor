from __future__ import annotations

from typing import Any, Mapping

REQUIRED_FIELDS = (
    "patient_id", "onset", "course", "severity", "associated_symptoms",
    "measurements", "current_medications", "allergies", "attempts", "red_flags",
)


def validate(payload: Mapping[str, Any]) -> list[str]:
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if "red_flags" in payload and not isinstance(payload["red_flags"], dict):
        missing.append("red_flags_must_be_object")
    severity = payload.get("severity")
    if severity is not None and not isinstance(severity, (int, float, str)):
        missing.append("severity_invalid")
    return missing
