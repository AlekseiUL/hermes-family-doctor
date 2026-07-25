from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from .intake import validate as validate_general_fields
from .interactions import InteractionService
from .query_planner import MEDICATION_INGREDIENTS


@dataclass(frozen=True)
class ValidationResult:
    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()
    stops: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ("general_education",)

    @property
    def patient_specific_allowed(self) -> bool:
        return not (self.missing or self.invalid or self.stops)

    def as_dict(self) -> dict[str, Any]:
        return {
            "missing": list(self.missing),
            "invalid": list(self.invalid),
            "stops": list(self.stops),
            "patient_specific_allowed": self.patient_specific_allowed,
            "general_education_allowed": True,
            "allowed_actions": list(self.allowed_actions),
            "exact_otc_label_directions_allowed": (
                self.patient_specific_allowed
                and "exact_otc_label_directions" in self.allowed_actions
            ),
        }


def _missing(payload: Mapping[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [f"missing:{field}" for field in fields if field not in payload or payload[field] in (None, "", [])]


def validate_general_symptom(payload: Mapping[str, Any]) -> ValidationResult:
    codes = validate_general_fields(payload)
    return ValidationResult(
        missing=tuple(sorted(f"missing:{code}" for code in codes if not code.endswith("_invalid") and not code.endswith("_must_be_object"))),
        invalid=tuple(sorted(f"invalid:{code}" for code in codes if code.endswith("_invalid") or code.endswith("_must_be_object"))),
    )


MEDICATION_COMMON_FIELDS = (
    "use_case",
    "patient_id", "exact_product_or_ingredient", "formulation_or_concentration", "route",
    "current_dose", "frequency", "indication", "medicines_and_supplements",
    "allergies_and_reactions", "age", "pregnancy_breastfeeding_relevance",
    "renal_relevance", "hepatic_relevance", "candidate_ingredient_id",
    "current_ingredient_ids", "interaction_identity_status", "supplements_present",
)
MEDICATION_USE_CASES = frozenset({
    "current_medication_review",
    "new_otc_self_care",
    "prescription_review",
})


def validate_medication_context(
    payload: Mapping[str, Any], *, interaction_review: Mapping[str, Any] | None = None,
) -> ValidationResult:
    missing = _missing(payload, MEDICATION_COMMON_FIELDS)
    for confirmed_list_field in (
        "medicines_and_supplements", "allergies_and_reactions", "current_ingredient_ids",
    ):
        if payload.get(confirmed_list_field) == []:
            missing = [item for item in missing if item != f"missing:{confirmed_list_field}"]
    use_case = payload.get("use_case")
    if use_case in {"current_medication_review", "prescription_review"}:
        missing.extend(_missing(payload, ("prescriber_instruction",)))
    age = payload.get("age")
    invalid: list[str] = []
    if use_case not in (None, "") and use_case not in MEDICATION_USE_CASES:
        invalid.append("invalid:use_case")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0):
        invalid.append("invalid:age")
    if payload.get("interaction_identity_status") not in (None, "", "confirmed_complete", "incomplete_or_unknown"):
        invalid.append("invalid:interaction_identity_status")
    if payload.get("supplements_present") not in (None, True, False, "unknown"):
        invalid.append("invalid:supplements_present")
    current_ingredient_ids = payload.get("current_ingredient_ids")
    if current_ingredient_ids is not None and not isinstance(current_ingredient_ids, list):
        invalid.append("invalid:current_ingredient_ids")
    child_relevant = payload.get("patient_type") == "child" or (isinstance(age, (int, float)) and not isinstance(age, bool) and age < 18)
    if child_relevant:
        missing.extend(_missing(payload, ("current_weight", "weight_measured_at")))
        invalid.extend(_weight_date_codes(payload.get("weight_measured_at"), payload.get("now")))
        weight = payload.get("current_weight")
        if weight is not None and (
            isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0
        ):
            invalid.append("invalid:current_weight")
    for field in ("pregnancy_breastfeeding_relevance", "renal_relevance", "hepatic_relevance"):
        value = payload.get(field)
        if value not in (None, True, False, "unknown"):
            invalid.append(f"invalid:{field}_must_be_boolean_or_unknown")
    stops = list(high_risk_stops(payload))
    if use_case == "new_otc_self_care" and child_relevant:
        stops.append("stop:pediatric_otc_requires_separate_validated_path")
    medicines = payload.get("medicines_and_supplements")
    current_ids = current_ingredient_ids if isinstance(current_ingredient_ids, list) else []
    if payload.get("interaction_identity_status") != "confirmed_complete":
        stops.append("stop:interaction_identity_incomplete")
    if isinstance(medicines, list) and len(current_ids) < len(medicines):
        stops.append("stop:interaction_identity_incomplete")
    if payload.get("supplements_present") in (True, "unknown"):
        stops.append("stop:supplement_interaction_coverage")
    if interaction_review is None:
        interaction_review = InteractionService(MEDICATION_INGREDIENTS).review(
            payload.get("candidate_ingredient_id"), current_ids,
        )
    if not interaction_review.get("valid"):
        stops.append("stop:interaction_review_invalid")
        invalid.extend(str(item) for item in interaction_review.get("errors", []) if str(item).startswith("invalid:"))
    if interaction_review.get("duplicate_ingredient_ids"):
        stops.append("stop:duplicate_active_ingredient")
    if any(
        finding.get("kind") == "drug_drug_interaction"
        for finding in interaction_review.get("findings", [])
        if isinstance(finding, Mapping)
    ):
        stops.append("stop:interaction_finding_requires_review")
    if not interaction_review.get("complete_drug_drug_pair_check"):
        stops.append("stop:incomplete_interaction_source")

    missing_result = tuple(sorted(set(missing)))
    invalid_result = tuple(sorted(set(invalid)))
    stop_result = tuple(sorted(set(stops)))
    patient_specific_allowed = not (missing_result or invalid_result or stop_result)
    allowed_actions = ["general_education"]
    if patient_specific_allowed:
        allowed_actions.append("patient_specific_medication_review")
        if use_case == "new_otc_self_care" and isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 18:
            allowed_actions.append("exact_otc_label_directions")
    return ValidationResult(
        missing_result,
        invalid_result,
        stop_result,
        tuple(allowed_actions),
    )


PEDIATRIC_FIELDS = ("patient_id", "patient_type", "date_of_birth", "guardian_authority")
OTC_WEIGHT_FIELDS = ("current_weight", "weight_measured_at", "formulation_or_concentration", "maximum_label_dose")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _weight_date_codes(value: Any, now_value: Any = None, max_age_days: int = 30) -> list[str]:
    measured = _parse_date(value)
    if measured is None:
        return ["invalid:weight_measured_at"] if value not in (None, "") else []
    now_date = _parse_date(now_value) or datetime.now(timezone.utc).date()
    if measured > now_date:
        return ["invalid:weight_measured_at_future"]
    if (now_date - measured).days > max_age_days:
        return ["invalid:weight_stale"]
    return []


def validate_pediatric_context(payload: Mapping[str, Any]) -> ValidationResult:
    missing = _missing(payload, PEDIATRIC_FIELDS)
    invalid: list[str] = []
    if payload.get("patient_type") not in (None, "", "child"):
        invalid.append("invalid:not_child_namespace")
    if payload.get("guardian_authority") is False:
        invalid.append("invalid:guardian_authority_required")
    elif payload.get("guardian_authority") not in (None, True):
        invalid.append("invalid:guardian_authority")
    if payload.get("date_of_birth") not in (None, "") and _parse_date(payload.get("date_of_birth")) is None:
        invalid.append("invalid:date_of_birth")
    if payload.get("weight_based_reasoning") is True:
        missing.extend(_missing(payload, OTC_WEIGHT_FIELDS))
        invalid.extend(_weight_date_codes(payload.get("weight_measured_at"), payload.get("now")))
        weight = payload.get("current_weight")
        if weight is not None and (
            isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0
        ):
            invalid.append("invalid:current_weight")
    stops = list(high_risk_stops(payload))
    return ValidationResult(tuple(sorted(set(missing))), tuple(sorted(set(invalid))), tuple(sorted(set(stops))))


def high_risk_stops(payload: Mapping[str, Any]) -> tuple[str, ...]:
    stops: set[str] = set()
    direct = (
        (("pregnancy_relevant", "pregnancy_breastfeeding_relevance"), "stop:pregnancy_relevance"),
        (("renal_relevant", "renal_relevance"), "stop:renal_relevance"),
        (("hepatic_relevant", "hepatic_relevance"), "stop:hepatic_relevance"),
        (("uncertain_ingestion",), "stop:uncertain_ingestion"),
        (("infant_or_severe_deterioration",), "stop:infant_or_severe_deterioration"),
        (("prescription_change_requested",), "stop:prescription_change"),
    )
    for fields, code in direct:
        if any(payload.get(field) is True for field in fields):
            stops.add(code)
        if any(payload.get(field) == "unknown" for field in fields):
            stops.add(f"{code}_unknown")
    medicines = payload.get("medicines_and_supplements")
    if isinstance(medicines, list) and len(medicines) >= 5:
        stops.add("stop:polypharmacy")
    elif medicines == "unknown":
        stops.add("stop:medication_list_unknown")
    if payload.get("allergies_and_reactions") == "unknown":
        stops.add("stop:allergy_status_unknown")
    if payload.get("use_case") == "new_otc_self_care" and (
        payload.get("current_dose") == "unknown" or payload.get("frequency") == "unknown"
    ):
        stops.add("stop:recent_dose_unknown")
    return tuple(sorted(stops))
