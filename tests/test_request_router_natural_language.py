from __future__ import annotations

import unittest

from family_health.request_router import classify_request


EMERGENCY_CASES = (
    "У меня давит в груди",
    "Сжимает за грудиной",
    "Сильная боль в груди",
    "В груди тяжесть и холодный пот",
    "Муж говорит, что грудь сильно сжимает",
    "Резко появилась тяжесть в груди",
    "Очень сильно болит за грудиной",
    "Не могу дышать",
    "Очень трудно нормально вдохнуть",
    "Я задыхаюсь",
    "У человека синеют губы",
    "Невозможно вдохнуть",
    "Малыш задыхается",
    "Человек потерял сознание",
    "Она без сознания",
    "Он не реагирует",
    "Муж не отвечает на голос",
    "Ребенок не приходит в сознание",
    "У ребенка начались судороги",
    "Судороги не прекращаются",
    "Снова начались судороги",
    "Идут судороги уже две минуты",
    "Судороги повторяются",
    "Онемела половина лица и рука",
    "Внезапно перекосило лицо",
    "Стала неразборчивая речь",
    "Не может поднять одну руку",
    "Ослабла половина тела",
    "Онемела рука и половина лица",
    "Кровь не останавливается",
    "Сильное кровотечение",
    "Кровотечение не прекращается",
    "Кровь льется очень сильно",
    "После пореза кровь не останавливается",
    "Отекло горло",
    "Опух язык",
    "После лекарства отекают губы",
    "Анафилаксия после укола",
    "Аллергия, отекает горло и трудно дышать",
    "Я хочу убить себя",
    "Боюсь, что могу навредить себе",
    "Собираюсь покончить с собой",
    "Могу причинить вред другому человеку",
)

SAME_DAY_CASES = (
    "Мне трудно дышать",
    "Стало тяжелее дышать",
    "Не хватает воздуха",
    "Появилась одышка",
    "Сегодня хуже дышится",
    "Не могу пить",
    "Ребенок почти не пьет",
    "Мало мочи и нарастает слабость",
    "Со вчера нет мочи",
    "Сухо во рту и не получается пить",
    "Высокая температура и мне хуже",
    "Лихорадка, слабость нарастает",
    "Температура держится, ребенок очень вялый",
    "Высокая температура и состояние ухудшается",
    "Лихорадка, сегодня резко хуже",
    "Сегодня впервые двоится в глазах",
    "Новая слабость в ногах",
    "Внезапно нарушилась координация",
    "Сегодня впервые появилась необычная слабость",
    "После падения сильная боль",
    "После удара не могу двигать рукой",
    "Сильная травма ноги",
    "Не могу наступить после падения",
    "После аварии болит шея",
    "Во время беременности появилась боль",
    "Беременность, началось кровотечение",
    "Во время беременности мне становится хуже",
    "Беременна, давление сильно поднялось",
    "Ребенок очень вялый и выглядит серьезно больным",
    "Малыш резко хуже себя чувствует",
    "Младенец не реагирует как обычно",
    "Ребенок серьезно болен и очень вялый",
    "Давление 190 на 120",
    "АД 185/121",
    "Давление 181 на 110",
    "Показало 170/125",
    "Сейчас давление 200 на 100",
)

ACUTE_CASES = (
    "У меня тошнота и слабость",
    "Резко стало очень плохо",
    "Сегодня болит живот",
    "Со вчера кашель",
    "Появилась сыпь",
    "Меня рвет",
    "Кружится голова",
    "Ломит тело",
    "Заложило ухо",
    "Сейчас озноб",
    "У меня пульс выше обычного",
    "Мне нехорошо",
    "Слушай, меня тошнит",
    "Я чувствую слабость",
    "У мужа болит спина",
    "У жены появилась сыпь",
    "У мамы кружится голова",
    "У папы кашель",
    "Пациент жалуется на боль",
    "Не магу нормально дышать",
    "Потирял сознание и уже пришел в себя",
    "Онемла рука",
    "Кров не останавливается",
    "Судорги у ребенка",
    "Давл 190/120",
    "С утра болит горло",
    "Сегодня живот крутит",
    "Подташнивает второй час",
    "Всё ещё температура",
    "Становится хуже",
)

PEDIATRIC_CASES = (
    "У ребенка сыпь",
    "Сына рвет",
    "Дочка кашляет",
    "Малыш очень вялый",
    "У младенца плохо с аппетитом",
    "Ребенок капризный и плохо спал",
    "У сына болит живот",
    "У дочери температура",
    "Малыш сегодня слабый",
    "Ребёнку стало плохо",
    "У ребенка странное состояние",
    "Младенец ведет себя не как обычно",
)

GENERAL_EDUCATION_CASES = (
    "Что такое боль в груди?",
    "Какие признаки инсульта?",
    "Расскажи про судороги",
    "Объясни, почему бывает одышка",
    "Какие симптомы анафилаксии?",
    "Что означает давление 190 на 120?",
    "Что такое потеря сознания?",
    "Расскажи о кровотечениях",
    "Что такое тошнота?",
    "Чем отличается тошнота от рвоты?",
    "Почему возникает слабость?",
    "Какие бывают причины головокружения?",
    "Как устроен иммунитет?",
    "Для чего нужен скрининг?",
    "Что такое высокая температура?",
    "Расскажи про детскую сыпь",
    "Объясни, почему немеет рука",
    "Какие основные симптомы обезвоживания?",
    "Что означает слово сатурация?",
    "Чем отличается обморок от потери сознания?",
    "Почему возникает кашель?",
    "Что такое аллергическая реакция?",
    "Расскажи о высоком давлении",
    "Какие есть причины боли в животе?",
    "Как устроен пульс?",
)

STABLE_ROUTE_CASES = (
    ("У ребёнка была операция", "historical_record"),
    ("Раньше у меня была аллергия", "historical_record"),
    ("В прошлом был перелом", "historical_record"),
    ("Мне удаляли аппендикс", "historical_record"),
    ("История госпитализации", "historical_record"),
    ("Перенес инсульт пять лет назад", "historical_record"),
    ("Совместимы ли сертралин и ибупрофен?", "medication"),
    ("Как принимать эту таблетку?", "medication"),
    ("Пью сертралин, проверь взаимодействия", "medication"),
    ("Какая доза препарата указана на упаковке?", "medication"),
    ("Посмотри анализ крови", "lab_document"),
    ("Есть новый результат лаборатории", "lab_document"),
    ("Разбери заключение врача", "lab_document"),
    ("Я пришлю PDF с анализами", "lab_document"),
    ("Нужна профилактика сердечных болезней", "prevention"),
    ("Какие прививки нужны взрослому?", "prevention"),
    ("Пора пройти скрининг", "prevention"),
    ("Составь план чекапа", "prevention"),
)


class NaturalLanguageRoutingTests(unittest.TestCase):
    def test_corpus_is_large_enough_to_guard_real_russian_phrasing(self):
        total = sum(map(len, (
            EMERGENCY_CASES,
            SAME_DAY_CASES,
            ACUTE_CASES,
            PEDIATRIC_CASES,
            GENERAL_EDUCATION_CASES,
            STABLE_ROUTE_CASES,
        )))
        self.assertGreaterEqual(total, 150)

    def test_emergency_phrases_cannot_enter_general_education(self):
        for text in EMERGENCY_CASES:
            with self.subTest(text=text):
                result = classify_request(text)
                self.assertIn(result["route"], {"acute_current_symptom", "pediatrics_current"})
                self.assertTrue(result["urgency_screen_required"])
                self.assertEqual("emergency_now", result["emergency_precheck"]["minimum_urgency"])
                self.assertFalse(result["emergency_precheck"]["model_may_downgrade"])

    def test_same_day_phrases_require_acute_triage(self):
        for text in SAME_DAY_CASES:
            with self.subTest(text=text):
                result = classify_request(text)
                self.assertIn(result["route"], {"acute_current_symptom", "pediatrics_current"})
                self.assertTrue(result["urgency_screen_required"])
                self.assertIn(
                    result["emergency_precheck"]["minimum_urgency"],
                    {"same_day", "emergency_now"},
                )

    def test_other_natural_and_typo_complaints_still_require_triage(self):
        for text in (*ACUTE_CASES, *PEDIATRIC_CASES):
            with self.subTest(text=text):
                result = classify_request(text)
                self.assertIn(result["route"], {"acute_current_symptom", "pediatrics_current"})
                self.assertTrue(result["urgency_screen_required"])

    def test_explicit_educational_questions_do_not_overtrigger(self):
        for text in GENERAL_EDUCATION_CASES:
            with self.subTest(text=text):
                result = classify_request(text)
                self.assertEqual("general_education", result["route"])
                self.assertFalse(result["urgency_screen_required"])
                self.assertEqual("none", result["emergency_precheck"]["minimum_urgency"])

    def test_stable_workflows_keep_their_specialist_routes(self):
        for text, expected in STABLE_ROUTE_CASES:
            with self.subTest(text=text):
                self.assertEqual(expected, classify_request(text)["route"])

    def test_unknown_patient_specific_request_fails_into_clarification_not_education(self):
        result = classify_request("У меня странное состояние, не знаю как объяснить")

        self.assertEqual("acute_current_symptom", result["route"])
        self.assertTrue(result["clarification_required"])
        self.assertTrue(result["urgency_screen_required"])

    def test_action_question_about_emergency_is_not_mistaken_for_education(self):
        result = classify_request("Что делать, если человек потерял сознание?")

        self.assertEqual("acute_current_symptom", result["route"])
        self.assertEqual("emergency_now", result["emergency_precheck"]["minimum_urgency"])


if __name__ == "__main__":
    unittest.main()
