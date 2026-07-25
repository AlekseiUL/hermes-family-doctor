from __future__ import annotations

import unittest

from family_health.request_router import classify_request


class RequestRouterTests(unittest.TestCase):
    def test_historical_surgery_bypasses_acute_triage_and_course_memory(self):
        result = classify_request(
            "Ребёнку раньше делали операцию. Что об этом скажешь?",
            patient_type="child",
        )

        self.assertEqual("historical_record", result["route"])
        self.assertFalse(result["urgency_screen_required"])
        self.assertFalse(result["course_memory_required"])
        self.assertEqual(["surgery", "hospitalization", "condition"], result["fact_categories"])

    def test_child_fever_with_dose_question_keeps_triage_and_medication_gate(self):
        result = classify_request(
            "У ребенка сейчас температура, какую дозу ибупрофена дать?",
            patient_type="child",
        )

        self.assertEqual("pediatrics_current", result["route"])
        self.assertTrue(result["urgency_screen_required"])
        self.assertTrue(result["course_memory_required"])
        self.assertTrue(result["medication_gate_required"])

    def test_stable_interaction_question_uses_medication_route_without_acute_course(self):
        result = classify_request("Совместимы ли сертралин и ибупрофен?")

        self.assertEqual("medication", result["route"])
        self.assertFalse(result["urgency_screen_required"])
        self.assertFalse(result["course_memory_required"])
        self.assertTrue(result["medication_gate_required"])


if __name__ == "__main__":
    unittest.main()
