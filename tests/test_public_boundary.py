from __future__ import annotations

import unittest
from pathlib import Path

from family_health.interactions import InteractionService
from family_health.query_planner import MEDICATION_INGREDIENTS


ROOT = Path(__file__).resolve().parents[1]


class PublicExtractionBoundaryTests(unittest.TestCase):
    def test_no_dataset_importer_is_distributed(self):
        self.assertFalse((ROOT / "src" / "family_health" / "ddinter_import.py").exists())
        source = ROOT / "runtime-template"
        packaged = ROOT / "src" / "family_health" / "resources" / "runtime-template"
        for path in source.rglob("*"):
            if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts:
                self.assertEqual(path.read_bytes(), (packaged / path.relative_to(source)).read_bytes())

    def test_no_database_or_downloaded_dataset_is_distributed(self):
        forbidden = {".sqlite", ".sqlite3", ".db", ".csv"}
        self.assertEqual([], [path for path in ROOT.rglob("*") if path.is_file() and path.suffix.casefold() in forbidden])

    def test_missing_optional_interaction_provider_fails_closed(self):
        result = InteractionService(MEDICATION_INGREDIENTS).review("ibuprofen", ["acetaminophen"])
        self.assertFalse(result["complete_drug_drug_pair_check"])
        self.assertFalse(result["overall_medication_safety_cleared"])
        self.assertEqual("coverage_incomplete", result["disposition"])


if __name__ == "__main__":
    unittest.main()
