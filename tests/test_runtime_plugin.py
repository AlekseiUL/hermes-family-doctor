from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from family_health.interactions import DDI_SCHEMA_VERSION
from family_health.store import HealthStore


PLUGIN_PATH = (
    Path(__file__).parents[1]
    / "runtime-template"
    / "plugins"
    / "family-health-evidence"
    / "__init__.py"
)
RUNTIME_ROOT = PLUGIN_PATH.parents[2]


def load_plugin():
    spec = importlib.util.spec_from_file_location("family_health_evidence_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def build_test_ddi_provider(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE interactions(
                ingredient_a TEXT NOT NULL,
                ingredient_b TEXT NOT NULL,
                severity TEXT NOT NULL,
                evidence_level TEXT,
                mechanism TEXT,
                management TEXT,
                source_id TEXT,
                source_record_id TEXT
            );
            """
        )
        metadata = {
            "schema_version": DDI_SCHEMA_VERSION,
            "provider_id": "synthetic-owner-approved",
            "dataset_version": "synthetic-2026-07",
            "coverage_complete": "true",
            "license_status": "owner_approved",
            "valid_until": (date.today() + timedelta(days=30)).isoformat(),
            "supported_ingredient_ids": json.dumps(["acetaminophen", "warfarin"]),
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
        connection.execute(
            """
            INSERT INTO interactions(
                ingredient_a, ingredient_b, severity, evidence_level,
                mechanism, management, source_id, source_record_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "acetaminophen", "warfarin", "moderate", "synthetic-evidence",
                "Synthetic test mechanism.", "Synthetic test management.",
                "synthetic-source", "synthetic-record",
            ),
        )
        connection.commit()


class RuntimePluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "hermes-family-doctor"
        db_path = self.home / "private" / "health-records.sqlite3"
        store = HealthStore(db_path)
        store.init()
        store.upsert_patient({"id": "synthetic-adult", "display_name": "Synthetic Adult"})
        self.env = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "HERMES_PROFILE": "hermes-family-doctor",
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_USER_ID": "private-user-123",
            "HERMES_SESSION_ID": "private-session-456",
        }, clear=False)
        self.env.start()
        self.plugin = load_plugin()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_registers_only_the_narrow_family_health_toolset(self):
        ctx = FakeContext()
        self.plugin.register(ctx)
        self.assertEqual(14, len(ctx.tools))
        self.assertEqual({"family_health"}, {tool["toolset"] for tool in ctx.tools})
        self.assertEqual({name for name, _schema, _handler in self.plugin.TOOLS}, {tool["name"] for tool in ctx.tools})
        for tool in ctx.tools:
            self.assertFalse(tool["schema"]["parameters"].get("additionalProperties", True))

    def test_new_evidence_routes_are_registered_with_typed_public_only_inputs(self):
        search_routes = self.plugin.SEARCH_SCHEMA["parameters"]["properties"]["route"]["enum"]
        self.assertTrue({"full_text", "lactation", "label_fallback", "public_health"} <= set(search_routes))
        record_types = self.plugin.RECORD_SCHEMA["parameters"]["properties"]["record_type"]["enum"]
        self.assertIn("pmc_pmcid", record_types)
        page_sources = self.plugin.OFFICIAL_PAGE_SCHEMA["parameters"]["properties"]["source_id"]["enum"]
        self.assertEqual({"who", "uspstf", "rch", "ema", "lactmed"}, set(page_sources))

    def test_official_page_tool_forwards_only_typed_public_record_fields(self):
        expected = {"success": True, "records": [{"section_or_record_id": "RCH:Synthetic"}]}
        request = {
            "source_id": "rch",
            "url": "https://www.rch.org.au/clinicalguide/guideline_index/Synthetic/",
            "record_id": "RCH:Synthetic",
        }
        with patch.object(self.plugin, "_evidence_service") as factory:
            factory.return_value.fetch_official_page.return_value = expected
            result = json.loads(self.plugin.medical_official_page_fetch(request))
        self.assertEqual(expected, result)
        factory.return_value.fetch_official_page.assert_called_once_with(request)

    def test_patient_capability_is_bound_to_runtime_identity_not_model_fields(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "record_summary",
        }, session_id="model-supplied-session"))
        self.assertTrue(selected["success"])
        summary = json.loads(self.plugin.family_health_patient_summary({
            "capability": selected["capability"], "patient_id": "synthetic-adult",
            "categories": ["condition", "allergy"],
        }))
        self.assertTrue(summary["success"])
        self.assertEqual([], summary["facts"])
        self.assertNotIn("private-user-123", json.dumps(selected))
        self.assertNotIn("private-session-456", json.dumps(selected))

    def test_patient_tool_self_migrates_legacy_store_before_issuing_capability(self):
        legacy_home = Path(self.tmp.name) / "legacy-family-doctor"
        legacy_store = HealthStore(legacy_home / "private" / "health-records.sqlite3")
        legacy_store.init()
        legacy_store.upsert_patient({"id": "legacy-adult", "display_name": "Legacy Adult"})
        with legacy_store.connect() as db:
            db.execute("DROP TABLE confirmation_receipts")
            db.execute("DROP TABLE capabilities")

        with patch.dict(os.environ, {"HERMES_HOME": str(legacy_home)}, clear=False):
            selected = json.loads(self.plugin.family_health_select_patient({
                "patient_id": "legacy-adult", "purpose": "record_summary",
            }))

        self.assertTrue(selected["success"])
        with legacy_store.connect() as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("capabilities", tables)
        self.assertIn("confirmation_receipts", tables)

    def test_patient_summary_requires_targeted_categories(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "record_summary",
        }))
        result = json.loads(self.plugin.family_health_patient_summary({
            "capability": selected["capability"], "patient_id": "synthetic-adult",
        }))
        self.assertFalse(result["success"])
        self.assertEqual("authorization_denied", result["error"])

    def test_card_update_requires_record_update_capability_and_explicit_confirmation(self):
        read_only = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "record_summary",
        }))
        denied = json.loads(self.plugin.family_health_propose_card_update({
            "capability": read_only["capability"],
            "patient_id": "synthetic-adult",
            "update": {
                "category": "allergy",
                "fact_key": "synthetic_allergy",
                "value": {"substance": "synthetic", "reaction": "synthetic"},
                "source": "owner_confirmed",
                "author": "owner",
                "last_verified": date.today().isoformat(),
            },
        }))
        self.assertFalse(denied["success"])

        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "record_update",
        }))
        proposal = json.loads(self.plugin.family_health_propose_card_update({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "update": {
                "category": "allergy",
                "fact_key": "synthetic_allergy",
                "value": {"substance": "synthetic", "reaction": "synthetic"},
                "source": "owner_confirmed",
                "author": "owner",
                "last_verified": date.today().isoformat(),
            },
        }))
        self.assertTrue(proposal["success"])
        self.assertFalse(proposal["write_applied"])

        before = json.loads(self.plugin.family_health_patient_summary({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "categories": ["allergy"],
        }))
        self.assertEqual([], before["facts"])

        confirmed = json.loads(self.plugin.family_health_confirm_card_update({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "pending_id": proposal["pending_id"],
            "preview_hash": proposal["preview_hash"],
            "confirmed": True,
        }))
        self.assertTrue(confirmed["success"])
        self.assertTrue(confirmed["write_applied"])

        after = json.loads(self.plugin.family_health_patient_summary({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "categories": ["allergy"],
        }))
        self.assertEqual("synthetic_allergy", after["facts"][0]["fact_key"])
        self.assertIsNone(after["next_reconfirmation_candidate"])

    def test_patient_summary_returns_at_most_one_reconfirmation_candidate(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "record_update",
        }))
        for fact_key in ("first_condition", "second_condition"):
            proposal = json.loads(self.plugin.family_health_propose_card_update({
                "capability": selected["capability"],
                "patient_id": "synthetic-adult",
                "update": {
                    "category": "condition", "fact_key": fact_key,
                    "value": {"name": fact_key}, "source": "owner_confirmed", "author": "owner",
                },
            }))
            confirmed = json.loads(self.plugin.family_health_confirm_card_update({
                "capability": selected["capability"], "patient_id": "synthetic-adult",
                "pending_id": proposal["pending_id"], "preview_hash": proposal["preview_hash"],
                "confirmed": True,
            }))
            self.assertTrue(confirmed["success"])

        summary = json.loads(self.plugin.family_health_patient_summary({
            "capability": selected["capability"], "patient_id": "synthetic-adult",
            "categories": ["condition"],
        }))
        self.assertEqual(2, summary["reconfirmation_recommended_count"])
        self.assertEqual(1, summary["reconfirmation_batch_limit"])
        self.assertIn(summary["next_reconfirmation_candidate"]["fact_key"], {"first_condition", "second_condition"})

    def test_illness_course_opens_continues_and_resolves_through_runtime_tool(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "symptom_review",
        }))
        intake = {
            "patient_id": "synthetic-adult",
            "onset": "yesterday",
            "course": "stable",
            "severity": 2,
            "associated_symptoms": ["synthetic cough"],
            "measurements": {},
            "current_medications": [],
            "allergies": [],
            "attempts": [],
            "course_tracking": {
                "action": "open_new",
                "tracking_confirmed": True,
                "complaint_key": "cough",
                "status": "active",
                "link_basis": "new_current_problem",
            },
            "red_flags": {flag: False for flag in self.plugin.ALL_FLAGS},
        }
        evaluated = json.loads(self.plugin.family_health_evaluate_intake({
            "capability": selected["capability"], "structured_intake": intake,
        }))
        self.assertTrue(evaluated["success"])
        self.assertEqual("opened", evaluated["course_tracking_status"])

        history = json.loads(self.plugin.family_health_recent_history({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "view": "all",
            "window_days": 90,
            "limit": 5,
            "complaint_key": "cough",
        }))
        self.assertTrue(history["success"])
        self.assertFalse(history["cross_patient_data"])
        self.assertEqual(1, history["course_count"])
        self.assertEqual(evaluated["course_id"], history["courses"][0]["id"])
        self.assertNotIn("patient_id", history)

        for action, status, link_basis, expected in (
            ("continue_existing", "recovering", "explicit_user_reference", "continued"),
            ("resolve_existing", "resolved", "explicit_recovery", "resolved"),
        ):
            followup = {
                **intake,
                "course": status,
                "course_tracking": {
                    "action": action,
                    "tracking_confirmed": True,
                    "complaint_key": "cough",
                    "course_id": evaluated["course_id"],
                    "status": status,
                    "link_basis": link_basis,
                },
            }
            result = json.loads(self.plugin.family_health_evaluate_intake({
                "capability": selected["capability"], "structured_intake": followup,
            }))
            self.assertEqual(expected, result["course_tracking_status"])

        final_history = json.loads(self.plugin.family_health_recent_history({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "view": "all", "window_days": 90, "limit": 5,
            "complaint_key": "cough",
        }))
        self.assertEqual("resolved", final_history["courses"][0]["status"])
        self.assertEqual(3, final_history["courses"][0]["update_count"])

    def test_pediatric_gate_is_separate_and_fails_closed_on_missing_context(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "pediatric_review",
        }))
        result = json.loads(self.plugin.family_health_pediatric_gate({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "context": {"patient_id": "synthetic-adult"},
        }))
        self.assertTrue(result["success"])
        self.assertFalse(result["patient_specific_allowed"])
        self.assertIn("missing:guardian_authority", result["missing"])

    def test_pediatric_gate_uses_outer_patient_id_and_rejects_conflicting_duplicate(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "pediatric_review",
        }))
        context = {
            "patient_type": "child",
            "date_of_birth": "2020-01-01",
            "age": 6,
            "guardian_authority": True,
            "current_weight": 20,
            "weight_measured_at": date.today().isoformat(),
            "formulation_or_concentration": "100 mg/5 mL",
            "maximum_label_dose": "synthetic label maximum",
        }

        accepted = json.loads(self.plugin.family_health_pediatric_gate({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "context": context,
        }))
        self.assertTrue(accepted["success"])
        self.assertTrue(accepted["patient_specific_allowed"])

        rejected = json.loads(self.plugin.family_health_pediatric_gate({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "context": {**context, "patient_id": "another-patient"},
        }))
        self.assertFalse(rejected["success"])
        self.assertEqual("authorization_denied", rejected["error"])

    def test_complete_adult_otc_gate_grants_only_exact_label_direction_permission(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "medication_review",
        }))
        result = json.loads(self.plugin.family_health_medication_gate({
            "capability": selected["capability"],
            "patient_id": "synthetic-adult",
            "context": {
                "use_case": "new_otc_self_care",
                "exact_product_or_ingredient": "acetaminophen",
                "formulation_or_concentration": "500 mg tablet",
                "route": "oral",
                "current_dose": "not_taking",
                "frequency": "not_taking",
                "indication": "mild headache",
                "medicines_and_supplements": [],
                "allergies_and_reactions": [],
                "age": 40,
                "pregnancy_breastfeeding_relevance": False,
                "renal_relevance": False,
                "hepatic_relevance": False,
                "candidate_ingredient_id": "acetaminophen",
                "current_ingredient_ids": [],
                "interaction_identity_status": "confirmed_complete",
                "supplements_present": False,
            },
        }))
        self.assertTrue(result["success"])
        self.assertTrue(result["exact_otc_label_directions_allowed"])
        self.assertIn("exact_otc_label_directions", result["allowed_actions"])
        self.assertTrue(result["interaction_review"]["complete_drug_drug_pair_check"])

    def test_current_drug_pair_and_duplicate_cannot_bypass_interaction_coverage(self):
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "medication_review",
        }))
        base = {
            "use_case": "new_otc_self_care",
            "exact_product_or_ingredient": "acetaminophen",
            "formulation_or_concentration": "500 mg tablet",
            "route": "oral", "current_dose": "not_taking", "frequency": "not_taking",
            "indication": "mild headache", "allergies_and_reactions": [], "age": 40,
            "pregnancy_breastfeeding_relevance": False,
            "renal_relevance": False, "hepatic_relevance": False,
            "candidate_ingredient_id": "acetaminophen",
            "interaction_identity_status": "confirmed_complete", "supplements_present": False,
        }
        incomplete = json.loads(self.plugin.family_health_medication_gate({
            "capability": selected["capability"], "patient_id": "synthetic-adult",
            "context": {
                **base, "medicines_and_supplements": ["warfarin"],
                "current_ingredient_ids": ["warfarin"],
            },
        }))
        self.assertIn("stop:incomplete_interaction_source", incomplete["stops"])
        self.assertFalse(incomplete["interaction_review"]["complete_drug_drug_pair_check"])

        duplicate = json.loads(self.plugin.family_health_medication_gate({
            "capability": selected["capability"], "patient_id": "synthetic-adult",
            "context": {
                **base, "medicines_and_supplements": ["second acetaminophen product"],
                "current_ingredient_ids": ["acetaminophen"],
            },
        }))
        self.assertIn("stop:duplicate_active_ingredient", duplicate["stops"])

    def test_owner_approved_ddi_provider_finding_blocks_runtime_medication_gate(self):
        ddi_path = Path(self.tmp.name) / "synthetic-ddi.sqlite3"
        build_test_ddi_provider(ddi_path)
        selected = json.loads(self.plugin.family_health_select_patient({
            "patient_id": "synthetic-adult", "purpose": "medication_review",
        }))
        context = {
            "use_case": "new_otc_self_care",
            "exact_product_or_ingredient": "acetaminophen",
            "formulation_or_concentration": "500 mg tablet",
            "route": "oral", "current_dose": "not_taking", "frequency": "not_taking",
            "indication": "mild headache",
            "medicines_and_supplements": ["warfarin"],
            "allergies_and_reactions": [], "age": 40,
            "pregnancy_breastfeeding_relevance": False,
            "renal_relevance": False, "hepatic_relevance": False,
            "candidate_ingredient_id": "acetaminophen",
            "current_ingredient_ids": ["warfarin"],
            "interaction_identity_status": "confirmed_complete", "supplements_present": False,
        }
        with patch.dict(os.environ, {"FAMILY_HEALTH_DDI_SQLITE": str(ddi_path)}, clear=False):
            result = json.loads(self.plugin.family_health_medication_gate({
                "capability": selected["capability"],
                "patient_id": "synthetic-adult", "context": context,
            }))
        self.assertTrue(result["interaction_review"]["complete_drug_drug_pair_check"])
        self.assertEqual("synthetic-owner-approved", result["interaction_review"]["provider"]["provider_id"])
        self.assertIn("stop:interaction_finding_requires_review", result["stops"])
        self.assertFalse(result["patient_specific_allowed"])

    def test_medication_schema_is_typed_and_rejects_model_invented_aliases(self):
        context_schema = self.plugin.MEDICATION_SCHEMA["parameters"]["properties"]["context"]
        self.assertFalse(context_schema["additionalProperties"])
        self.assertIn("exact_product_or_ingredient", context_schema["required"])
        self.assertIn("medicines_and_supplements", context_schema["properties"])
        self.assertNotIn("interaction_source_complete", context_schema["properties"])

        result = json.loads(self.plugin.family_health_medication_gate({
            "capability": "synthetic",
            "patient_id": "synthetic-adult",
            "context": {"age_years": 40, "active_medications": []},
        }))
        self.assertFalse(result["success"])
        self.assertEqual("unsupported_or_private_field", result["error"])

    def test_evidence_search_rejects_patient_or_raw_query_fields_before_network(self):
        for payload in (
            {"route": "guidelines", "query": "Synthetic Person asthma"},
            {"route": "guidelines", "concept_id": "asthma", "patient_id": "synthetic-adult"},
        ):
            with self.subTest(payload=payload), patch.object(self.plugin, "_evidence_service") as service:
                result = json.loads(self.plugin.medical_evidence_search(payload))
                self.assertFalse(result["success"])
                self.assertEqual("unsupported_or_private_field", result["error"])
                service.assert_not_called()

    def test_status_and_catalog_do_not_read_patient_values(self):
        status = json.loads(self.plugin.family_health_status({}))
        self.assertTrue(status["success"])
        self.assertEqual("1.1.0", status["version"])
        self.assertEqual("fail-safe-1.1", status["request_router_version"])
        self.assertTrue(status["deterministic_emergency_precheck"])
        self.assertFalse(status["router_minimum_urgency_may_be_downgraded"])
        self.assertEqual(19, status["source_registry_count"])
        self.assertEqual(13, len(status["executable_source_ids"]))
        self.assertFalse(status["complete_interaction_engine"])
        self.assertTrue(status["interaction_engine"]["local_duplicate_ingredient_check"])
        self.assertFalse(status["model_can_self_attest_interaction_coverage"])
        self.assertTrue(status["exact_adult_otc_label_directions"])
        self.assertTrue(status["illness_course_memory"])
        self.assertTrue(status["course_link_requires_confirmation"])
        self.assertFalse(status["pediatric_or_prescription_dose_calculation"])
        self.assertTrue(status["triage_requires_complete_screen"])
        self.assertEqual(16, len(status["triage_flag_ids"]))
        catalog = json.loads(self.plugin.medical_source_catalog({"view": "concepts"}))
        self.assertIn("asthma", catalog["controlled_concept_ids"])
        self.assertNotIn("facts", catalog)
        sources = json.loads(self.plugin.medical_source_catalog({"view": "sources"}))
        self.assertEqual(
            "requires_current_setting_confirmation",
            sources["local_applicability_status"],
        )
        self.assertNotIn("georgia", json.dumps(sources).casefold())

    def test_runtime_router_cannot_downgrade_natural_language_emergency(self):
        result = json.loads(self.plugin.family_health_classify_request({
            "text": "У меня давит в груди",
        }))

        self.assertTrue(result["success"])
        self.assertEqual("acute_current_symptom", result["route"])
        self.assertEqual("emergency_now", result["emergency_precheck"]["minimum_urgency"])
        self.assertIn(
            "severe_chest_pain_or_pressure",
            result["emergency_precheck"]["matched_flags"],
        )
        self.assertFalse(result["emergency_precheck"]["model_may_downgrade"])

    def test_intake_schema_exposes_strict_triage_vocabulary(self):
        intake = self.plugin.INTAKE_SCHEMA["parameters"]["properties"]["structured_intake"]
        red_flags = intake["properties"]["red_flags"]
        self.assertFalse(red_flags["additionalProperties"])
        self.assertEqual(set(self.plugin.ALL_FLAGS), set(red_flags["properties"]))
        self.assertIn("course_tracking", intake["required"])
        tracking = intake["properties"]["course_tracking"]
        self.assertFalse(tracking["additionalProperties"])
        self.assertEqual(
            set(self.plugin.COMPLAINT_TYPES),
            set(tracking["properties"]["complaint_key"]["enum"]),
        )

    def test_history_schema_is_bounded_and_strict(self):
        parameters = self.plugin.HISTORY_SCHEMA["parameters"]
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(10, parameters["properties"]["limit"]["maximum"])
        self.assertEqual(365, parameters["properties"]["window_days"]["maximum"])
        self.assertIn("capability", parameters["required"])

    def test_runtime_profile_stays_compact_and_self_curation_is_disabled(self):
        config = (RUNTIME_ROOT / "config.example.yaml").read_text(encoding="utf-8")
        self.assertIn("max_turns: 45", config)
        self.assertIn("disabled_toolsets:", config)
        self.assertIn("- terminal", config)
        self.assertIn("- memory", config)
        self.assertNotIn("enabled_toolsets:", config)
        self.assertNotIn("api_key", config.casefold())
        self.assertNotIn("telegram", config.casefold())

        expected = {
            "family-doctor-acute", "family-doctor-prevention",
            "family-doctor-labs", "family-doctor-longitudinal",
        }
        skill_dirs = {path.parent.name for path in (RUNTIME_ROOT / "skills").glob("*/SKILL.md")}
        self.assertTrue(expected <= skill_dirs)
        for name in expected:
            skill_path = RUNTIME_ROOT / "skills" / name / "SKILL.md"
            self.assertLess(skill_path.stat().st_size, 4_000)

        soul = (RUNTIME_ROOT / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("triage_coverage_complete", soul)
        self.assertIn("Стабильная профилактика", soul)
        self.assertIn("актуальное не переспрашивай", soul.casefold())
        self.assertIn("reasoning_allowed=true", soul)
        self.assertIn("family_health_recent_history", soul)
        self.assertIn("family_health_classify_request", soul)
        self.assertIn("минимальную срочность", soul.casefold())
        self.assertIn("не склеивай", soul.casefold())

        evidence = (RUNTIME_ROOT / "skills" / "family-doctor-evidence" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("medical_official_page_fetch", evidence)
        self.assertIn("openFDA", evidence)

        records = (RUNTIME_ROOT / "skills" / "family-doctor-records" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("next_reconfirmation_candidate", records)
        self.assertIn("family_health_confirm_card_update", records)

        medication = (RUNTIME_ROOT / "skills" / "family-doctor-medications" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("exact_otc_label_directions_allowed=true", medication)
        self.assertIn("Search the controlled ingredient once", medication)


if __name__ == "__main__":
    unittest.main()
