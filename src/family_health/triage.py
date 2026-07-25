from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

EMERGENCY_FLAGS = frozenset({
    "severe_chest_pain_or_pressure",
    "severe_breathing_difficulty",
    "unresponsive_or_fainting",
    "seizure_active_or_repeated",
    "stroke_signs",
    "severe_uncontrolled_bleeding",
    "anaphylaxis_signs",
    "immediate_self_harm_or_violence_risk",
})

SAME_DAY_FLAGS = frozenset({
    "severe_hypertension_reading",
    "new_or_worsening_breathing_problem",
    "dehydration_signs",
    "persistent_high_fever_with_deterioration",
    "new_neurological_symptom",
    "significant_injury",
    "pregnancy_urgent_concern",
    "child_appears_seriously_unwell",
})

ALL_FLAGS = EMERGENCY_FLAGS | SAME_DAY_FLAGS

@dataclass(frozen=True)
class TriageResult:
    level: str
    matched_flags: tuple[str, ...]
    unresolved_flags: tuple[str, ...] = ()
    unknown_flags: tuple[str, ...] = ()
    assessed_flags: tuple[str, ...] = ()
    rule_version: str = "engineering-prototype-0.4"
    clinician_validated: bool = False

    @property
    def coverage_complete(self) -> bool:
        return (
            not self.unresolved_flags
            and not self.unknown_flags
            and set(self.assessed_flags) == ALL_FLAGS
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "matched_flags": list(self.matched_flags),
            "unresolved_flags": list(self.unresolved_flags),
            "unknown_flags": list(self.unknown_flags),
            "assessed_flag_count": len(self.assessed_flags),
            "expected_flag_count": len(ALL_FLAGS),
            "coverage_complete": self.coverage_complete,
            "rule_version": self.rule_version,
            "clinician_validated": self.clinician_validated,
            "warning": "Prototype rules do not establish medical safety.",
        }


def evaluate(red_flags: Mapping[str, Any]) -> TriageResult:
    """Evaluate a complete explicit screen; missing or unknown input fails closed."""
    if not isinstance(red_flags, Mapping):
        return TriageResult("insufficient_data", (), ("red_flags_object_required",))

    provided = set(red_flags)
    unknown = tuple(sorted(str(key) for key in provided - ALL_FLAGS))
    assessed = tuple(sorted(
        key for key in provided & ALL_FLAGS if isinstance(red_flags[key], bool)
    ))
    invalid = {
        key for key in provided & ALL_FLAGS
        if red_flags[key] is not None and not isinstance(red_flags[key], bool)
    }
    unresolved = tuple(sorted(
        (ALL_FLAGS - set(assessed)) | invalid
    ))

    true_flags = {key for key in provided & ALL_FLAGS if red_flags[key] is True}
    emergency = tuple(sorted(true_flags & EMERGENCY_FLAGS))
    if emergency:
        return TriageResult("emergency_now", emergency, unresolved, unknown, assessed)
    same_day = tuple(sorted(true_flags & SAME_DAY_FLAGS))
    if same_day:
        return TriageResult("same_day", same_day, unresolved, unknown, assessed)
    if unresolved or unknown:
        return TriageResult("insufficient_data", (), unresolved, unknown, assessed)
    return TriageResult("no_rule_triggered", (), (), (), assessed)
