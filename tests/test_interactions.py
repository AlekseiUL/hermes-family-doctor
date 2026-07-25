from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from family_health.interactions import (
    DDI_SCHEMA_VERSION,
    InteractionService,
    SqliteInteractionProvider,
)


ALLOWED = {"acetaminophen", "ibuprofen", "warfarin"}


def build_provider(path: Path, *, complete: bool = True, approved: bool = True, expired: bool = False) -> None:
    valid_until = date.today() + timedelta(days=-1 if expired else 90)
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
            "provider_id": "synthetic-licensed-provider",
            "dataset_version": "2026-07-test",
            "coverage_complete": "true" if complete else "false",
            "license_status": "owner_approved" if approved else "unreviewed",
            "valid_until": valid_until.isoformat(),
            "supported_ingredient_ids": json.dumps(sorted(ALLOWED)),
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
                "acetaminophen", "warfarin", "moderate", "synthetic-level-1",
                "Synthetic interaction mechanism.", "Synthetic monitoring requirement.",
                "synthetic-source", "synthetic-record-1",
            ),
        )
        connection.commit()


def build_unapproved_signal_provider(path: Path) -> None:
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
            "provider_id": "unapproved-signal",
            "provider_mode": "signal_only",
            "dataset_version": "test",
            "coverage_complete": "false",
            "finding_use_allowed": "true",
            "license_status": "unreviewed",
            "supported_ingredient_ids": json.dumps(sorted(ALLOWED)),
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
                "acetaminophen", "warfarin", "major", "fixture",
                "fixture", "fixture", "fixture", "fixture",
            ),
        )
        connection.commit()


class InteractionServiceTests(unittest.TestCase):
    def test_single_ingredient_needs_no_pair_provider(self):
        result = InteractionService(ALLOWED).review("acetaminophen", [])
        self.assertTrue(result["valid"])
        self.assertTrue(result["complete_drug_drug_pair_check"])
        self.assertEqual(0, result["pair_count"])
        self.assertFalse(result["overall_medication_safety_cleared"])

    def test_duplicate_ingredient_is_always_a_blocking_finding(self):
        result = InteractionService(ALLOWED).review("acetaminophen", ["acetaminophen"])
        self.assertEqual(["acetaminophen"], result["duplicate_ingredient_ids"])
        self.assertEqual("blocked_duplicate_ingredient", result["disposition"])
        self.assertEqual("duplicate_active_ingredient", result["findings"][0]["kind"])

    def test_unconfigured_provider_never_turns_absence_into_clearance(self):
        result = InteractionService(ALLOWED).review("acetaminophen", ["warfarin"])
        self.assertFalse(result["complete_drug_drug_pair_check"])
        self.assertEqual(1, result["unresolved_pair_count"])
        self.assertEqual("coverage_incomplete", result["disposition"])

    def test_unknown_or_malformed_ingredients_fail_closed(self):
        result = InteractionService(ALLOWED).review("invented-drug", [None])
        self.assertFalse(result["valid"])
        self.assertFalse(result["complete_drug_drug_pair_check"])
        self.assertTrue(any("unknown_ingredient" in item for item in result["errors"]))

    def test_owner_approved_current_sqlite_provider_can_resolve_exact_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ddi.sqlite3"
            build_provider(path)
            service = InteractionService(ALLOWED, SqliteInteractionProvider(path))

            finding = service.review("acetaminophen", ["warfarin"])
            self.assertTrue(finding["complete_drug_drug_pair_check"])
            self.assertEqual(1, finding["finding_count"])
            self.assertEqual("moderate", finding["max_severity"])

            no_finding = service.review("ibuprofen", ["warfarin"])
            self.assertTrue(no_finding["complete_drug_drug_pair_check"])
            self.assertEqual(0, no_finding["finding_count"])
            self.assertEqual("requested_drug_drug_pairs_checked_no_finding", no_finding["disposition"])

    def test_stale_or_unapproved_provider_cannot_claim_complete_coverage(self):
        for kwargs in ({"expired": True}, {"approved": False}, {"complete": False}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "ddi.sqlite3"
                build_provider(path, **kwargs)
                result = InteractionService(ALLOWED, SqliteInteractionProvider(path)).review(
                    "ibuprofen", ["warfarin"],
                )
                self.assertFalse(result["complete_drug_drug_pair_check"])
                self.assertEqual(1, result["unresolved_pair_count"])

    def test_unapproved_signal_provider_cannot_surface_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ddi.sqlite3"
            build_unapproved_signal_provider(path)
            result = InteractionService(ALLOWED, SqliteInteractionProvider(path)).review(
                "acetaminophen", ["warfarin"],
            )
            self.assertEqual(0, result["finding_count"])
            self.assertFalse(result["complete_drug_drug_pair_check"])
            self.assertEqual(["ddi_provider_metadata_not_approved"], result["errors"])


if __name__ == "__main__":
    unittest.main()
