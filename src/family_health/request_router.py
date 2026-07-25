from __future__ import annotations

import re
from typing import Any


ROUTER_RULE_VERSION = "fail-safe-1.1"
REQUEST_ROUTES = frozenset({
    "acute_current_symptom",
    "historical_record",
    "medication",
    "pediatrics_current",
    "prevention",
    "lab_document",
    "general_education",
})

_HISTORICAL = re.compile(
    r"\b(?:был[аио]?|раньше|ранее|в прошлом|истори[яию]|операци[яию]|"
    r"выписк[аиу]|госпитализаци[яию]|перен[её]с|удаляли|делали операцию|"
    r"previous|history|surgery|hospitali[sz]ation|record)",
    re.IGNORECASE,
)
_CURRENT = re.compile(
    r"\b(?:сейчас|сегодня|вчера|со вчера|только что|внезапно|резко|начал|"
    r"началась|начались|продолжа|все еще|всё ещё|ухудш|хуже|нараста|current|"
    r"today|yesterday|just now|sudden|worsen)",
    re.IGNORECASE,
)
_SYMPTOM = re.compile(
    r"\b(?:боль|болит|давит|сжимает|жж[её]т|температур|лихорад|каш|рвот|рв[её]т|"
    r"тошн|подташн|понос|диаре|сып|одыш|дыш|задых|слабост|вял|головокруж|кружит|обмор|"
    r"сознани|судорог|кровотеч|кровь|онем|перекос|от[её]к|опух|травм|"
    r"пульс|давлени|сатурац|плохо|нехорошо|озноб|ломит|живот|залож|pain|fever|cough|vomit|"
    r"nausea|rash|weak|dizz|faint|seizure|bleed|numb|breath)",
    re.IGNORECASE,
)
_MEDICATION = re.compile(
    r"\b(?:лекарств|препарат|таблет|доз|совместим|взаимодейств|пью|принима|"
    r"medicine|medication|drug|tablet|dose|interaction)",
    re.IGNORECASE,
)
_DOCUMENT = re.compile(
    r"\b(?:анализ|результат|лаборатор|выписка|заключение|справка|pdf|фото|"
    r"document|report|lab|test result|scan)",
    re.IGNORECASE,
)
_PREVENTION = re.compile(
    r"\b(?:профилакти|скрининг|вакцин|привив|чекап|диспансер|"
    r"prevention|screening|vaccin|checkup)",
    re.IGNORECASE,
)
_CHILD = re.compile(
    r"\b(?:реб[её]нок|ребенк|детск|доч[ьи]|сын|малыш|младен|педиатр|"
    r"child|kid|infant|pediatric)",
    re.IGNORECASE,
)
_PATIENT_CONTEXT = re.compile(
    r"\b(?:у меня|у него|у нее|у ребёнка|у ребенка|у сына|у дочери|у дочки|"
    r"у мужа|у жены|у мамы|у папы|мо(?:я|й|е|ю|и)|пациент|человек|мне (?:плохо|трудно|"
    r"больно|нехорошо)|ему (?:плохо|трудно|больно)|ей (?:плохо|трудно|"
    r"больно)|я (?:чувствую|задыхаюсь|болею|упал|упала))",
    re.IGNORECASE,
)
_GENERAL_EDUCATION = re.compile(
    r"^\s*(?:что такое|что означает|объясни|расскажи (?:про|о)|чем отличается|"
    r"как устроен|почему возникает|для чего нужен|какие\s+"
    r"(?:(?:бывают|есть|основные)\s+)?(?:признаки|симптомы|причины)|может ли в целом)",
    re.IGNORECASE,
)
_ACTION_QUESTION = re.compile(
    r"\b(?:что делать|куда обращаться|вызывать ли|нужна ли скорая|как помочь|"
    r"помогите|срочно)",
    re.IGNORECASE,
)

_EMERGENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "severe_chest_pain_or_pressure",
        re.compile(
            r"(?:\b(?:давит|сжимает|тяжест[ьи]|сильн\w*\s+бол\w*)\b.{0,28}"
            r"\b(?:груд|за грудин)|\b(?:груд|за грудин).{0,28}\b(?:давит|"
            r"сжимает|тяжест[ьи]|сильн\w*\s+бол))",
            re.IGNORECASE,
        ),
    ),
    (
        "severe_breathing_difficulty",
        re.compile(
            r"\b(?:не могу|невозможно|очень трудно|очень тяжело)\s+(?:нормально\s+)?"
            r"(?:дышать|вдохнуть)|\bзадыха\w*|\bудуш\w*|\bсине\w*\s+губ|"
            r"\bне может\s+говорить.{0,24}(?:дыхан|воздух)",
            re.IGNORECASE,
        ),
    ),
    (
        "unresponsive_or_fainting",
        re.compile(
            r"\b(?:потерял\w*\s+сознани|без сознани|не приходит\w*\s+в сознани|"
            r"не реагиру\w*|не отвечает\w*\s+на (?:голос|обращение))",
            re.IGNORECASE,
        ),
    ),
    (
        "seizure_active_or_repeated",
        re.compile(
            r"(?:\b(?:начал\w*|идут|продолжа\w*|повторя\w*)\b.{0,24}\bсудорог|"
            r"\bсудорог\w*.{0,24}\b(?:не прекращ|снова|повтор))",
            re.IGNORECASE,
        ),
    ),
    (
        "stroke_signs",
        re.compile(
            r"(?:\b(?:онемел\w*|ослаб\w*)\b.{0,36}\b(?:половин|лиц|рук|ног)|"
            r"\bперекосил\w*.{0,18}\bлиц|\bнеразборчив\w*.{0,16}\bреч|"
            r"\bне может\s+поднять.{0,16}\bрук)",
            re.IGNORECASE,
        ),
    ),
    (
        "severe_uncontrolled_bleeding",
        re.compile(
            r"(?:\bкров\w*\b.{0,34}\b(?:не останавли|не прекращ|"
            r"льется|сильн)|\bсильн\w*.{0,20}\bкровотеч)",
            re.IGNORECASE,
        ),
    ),
    (
        "anaphylaxis_signs",
        re.compile(
            r"(?:\b(?:отека|отек|опух)\w*.{0,28}\b(?:горл|язык|губ)|"
            r"\bанафилак\w*|\bаллерг\w*.{0,36}\b(?:горл|язык|губ).{0,24}"
            r"\b(?:дыш|глот|отек))",
            re.IGNORECASE,
        ),
    ),
    (
        "immediate_self_harm_or_violence_risk",
        re.compile(
            r"\b(?:хочу|могу|собираюсь|боюсь,? что могу).{0,32}\b(?:убить себя|"
            r"покончить с собой|навредить себе|причинить вред)",
            re.IGNORECASE,
        ),
    ),
)

_SAME_DAY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "new_or_worsening_breathing_problem",
        re.compile(
            r"\b(?:трудн\w*|тяжел\w*|хуже)\s+(?:стало\s+)?дыш|\bне хватает\s+"
            r"воздух|\bодыш\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "dehydration_signs",
        re.compile(
            r"\b(?:не могу\s+пить|почти не пьет|мало моч\w*|нет моч\w*|сух\w*\s+во рту)",
            re.IGNORECASE,
        ),
    ),
    (
        "persistent_high_fever_with_deterioration",
        re.compile(
            r"\b(?:высок\w*\s+температур|лихорад\w*).{0,40}\b(?:ухудш|хуже|"
            r"вял\w*|нараста)",
            re.IGNORECASE,
        ),
    ),
    (
        "new_neurological_symptom",
        re.compile(
            r"\b(?:впервые|нов\w*|сегодня|внезапно).{0,28}\b(?:онемел|слабост|"
            r"наруш\w*\s+координац|двоится)",
            re.IGNORECASE,
        ),
    ),
    (
        "significant_injury",
        re.compile(
            r"\b(?:после\s+(?:падения|удара|аварии)|сильн\w*\s+травм|"
            r"не могу\s+(?:наступить|двигать))",
            re.IGNORECASE,
        ),
    ),
    (
        "pregnancy_urgent_concern",
        re.compile(
            r"\b(?:беремен\w*|во время беременности).{0,40}\b(?:боль|кров|"
            r"ухудш|хуже|плохо|давлени)",
            re.IGNORECASE,
        ),
    ),
    (
        "child_appears_seriously_unwell",
        re.compile(
            r"\b(?:ребен\w*|малыш|младен\w*).{0,36}\b(?:очень вял|не реагиру|"
            r"серьезно бол|резко хуже)",
            re.IGNORECASE,
        ),
    ),
)

_BLOOD_PRESSURE = re.compile(
    r"(?:\bдавлени\w*\s*)?(?P<systolic>\d{3})\s*(?:на|/)\s*(?P<diastolic>\d{2,3})\b",
    re.IGNORECASE,
)


def _precheck(normalized: str, *, enabled: bool) -> dict[str, Any]:
    emergency_flags: list[str] = []
    same_day_flags: list[str] = []
    if enabled:
        emergency_flags = [
            flag for flag, pattern in _EMERGENCY_PATTERNS if pattern.search(normalized)
        ]
        same_day_flags = [
            flag for flag, pattern in _SAME_DAY_PATTERNS if pattern.search(normalized)
        ]
        for match in _BLOOD_PRESSURE.finditer(normalized):
            systolic = int(match.group("systolic"))
            diastolic = int(match.group("diastolic"))
            if systolic >= 180 or diastolic >= 120:
                same_day_flags.append("severe_hypertension_reading")
                break

    emergency_flags = list(dict.fromkeys(emergency_flags))
    same_day_flags = [
        flag for flag in dict.fromkeys(same_day_flags) if flag not in emergency_flags
    ]
    if emergency_flags:
        minimum_urgency = "emergency_now"
    elif same_day_flags:
        minimum_urgency = "same_day"
    else:
        minimum_urgency = "none"
    return {
        "minimum_urgency": minimum_urgency,
        "matched_flags": [*emergency_flags, *same_day_flags],
        "immediate_instruction": (
            "Немедленно позвоните в местную экстренную службу. "
            "Не откладывайте помощь ради продолжения чата."
            if minimum_urgency == "emergency_now" else None
        ),
        "model_may_downgrade": False,
        "scope": "deterministic_lexical_precheck_not_full_triage",
        "warning": "Отсутствие совпадения не подтверждает безопасность.",
    }


def _result(
    route: str,
    *,
    matched_signals: list[str],
    medication_gate_required: bool = False,
    precheck: dict[str, Any] | None = None,
    clarification_required: bool = False,
) -> dict[str, Any]:
    acute = route in {"acute_current_symptom", "pediatrics_current"}
    categories = {
        "historical_record": ["surgery", "hospitalization", "condition"],
        "medication": ["medication", "allergy", "condition"],
        "pediatrics_current": ["condition", "allergy", "medication", "measurement"],
        "acute_current_symptom": ["condition", "allergy", "medication", "measurement"],
        "prevention": ["condition", "family_history", "allergy", "medication"],
        "lab_document": ["condition", "medication", "measurement"],
        "general_education": [],
    }[route]
    purpose = {
        "historical_record": "record_summary",
        "medication": "medication_review",
        "pediatrics_current": "pediatric_review",
        "acute_current_symptom": "symptom_review",
        "prevention": "prevention",
        "lab_document": "record_summary",
        "general_education": "medical_review",
    }[route]
    urgency_precheck = precheck or _precheck("", enabled=False)
    return {
        "route": route,
        "urgency_screen_required": acute,
        "course_memory_required": acute,
        "patient_selection_required": route != "general_education",
        "fact_categories": categories,
        "selection_purpose": purpose,
        "professional_module": {
            "historical_record": "records",
            "medication": "medications",
            "pediatrics_current": "pediatrics",
            "acute_current_symptom": "acute",
            "prevention": "prevention",
            "lab_document": "labs",
            "general_education": "core",
        }[route],
        "medication_gate_required": route == "medication" or medication_gate_required,
        "pediatric_gate_required": route == "pediatrics_current",
        "matched_signals": matched_signals[:6],
        "clarification_required": clarification_required,
        "emergency_precheck": urgency_precheck,
        "routing_rule_version": ROUTER_RULE_VERSION,
        "classification_scope": "workflow_and_fail_safe_urgency_precheck_no_diagnosis",
    }


def classify_request(text: str, *, patient_type: str = "unknown") -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
        raise ValueError("request_text_invalid")
    if patient_type not in {"adult", "child", "unknown"}:
        raise ValueError("patient_type_invalid")

    normalized = re.sub(r"\s+", " ", text.strip().casefold().replace("ё", "е"))
    historical = bool(_HISTORICAL.search(normalized))
    current = bool(_CURRENT.search(normalized))
    symptom = bool(_SYMPTOM.search(normalized))
    medication = bool(_MEDICATION.search(normalized))
    child = patient_type == "child" or bool(_CHILD.search(normalized))
    patient_context = bool(_PATIENT_CONTEXT.search(normalized))
    education = bool(_GENERAL_EDUCATION.search(normalized))
    action_question = bool(_ACTION_QUESTION.search(normalized))

    historical_only = historical and not current and not action_question
    precheck_enabled = not (education and not patient_context and not current and not action_question)
    precheck = _precheck(normalized, enabled=precheck_enabled and not historical_only)

    if education and not patient_context and not current and not action_question:
        return _result(
            "general_education",
            matched_signals=["explicit_education"],
            precheck=precheck,
        )

    if historical_only:
        return _result(
            "historical_record",
            matched_signals=["historical", "child" if child else "patient_unknown"],
            precheck=precheck,
        )

    if precheck["matched_flags"] or symptom or current:
        return _result(
            "pediatrics_current" if child else "acute_current_symptom",
            matched_signals=[
                "urgency_precheck" if precheck["matched_flags"] else "current_symptom",
                "child" if child else "adult_or_unknown",
                "medication" if medication else "no_medication",
                *precheck["matched_flags"],
            ],
            medication_gate_required=medication,
            precheck=precheck,
        )

    if medication:
        return _result("medication", matched_signals=["medication", "stable"], precheck=precheck)
    if _DOCUMENT.search(normalized):
        return _result("lab_document", matched_signals=["document_or_lab"], precheck=precheck)
    if _PREVENTION.search(normalized):
        return _result("prevention", matched_signals=["prevention"], precheck=precheck)
    if patient_context or child:
        return _result(
            "pediatrics_current" if child else "acute_current_symptom",
            matched_signals=["patient_specific_unknown_complaint", "child" if child else "adult_or_unknown"],
            precheck=precheck,
            clarification_required=True,
        )
    return _result(
        "general_education",
        matched_signals=["explicit_education" if education else "no_patient_specific_signal"],
        precheck=precheck,
    )
