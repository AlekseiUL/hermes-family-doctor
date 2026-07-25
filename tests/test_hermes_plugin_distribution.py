from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Callable, cast


ROOT = Path(__file__).resolve().parents[1]


class FakePluginContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict] = {}
        self.skills: dict[str, Path] = {}
        self.hooks: dict[str, Callable[..., object]] = {}

    def register_tool(self, *, name, **kwargs):
        self.tools[name] = kwargs

    def register_skill(self, name, path, description=""):
        del description
        self.skills[name] = Path(path)

    def register_hook(self, name, callback: Callable[..., object]):
        self.hooks[name] = callback


def load_root_plugin():
    spec = importlib.util.spec_from_file_location(
        "hermes_family_doctor_public_plugin",
        ROOT / "__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_is_a_directly_installable_hermes_plugin():
    assert (ROOT / "plugin.yaml").is_file()
    assert (ROOT / "__init__.py").is_file()

    ctx = FakePluginContext()
    plugin = load_root_plugin()
    plugin.register(ctx)

    required_tools = {
        "family_health_status",
        "family_health_classify_request",
        "family_health_select_patient",
        "family_health_patient_summary",
        "family_health_evaluate_intake",
        "family_health_medication_gate",
        "medical_evidence_search",
    }
    assert required_tools <= set(ctx.tools)
    assert "family-doctor" in ctx.skills
    assert "family-doctor-records" in ctx.skills
    assert all(path.name == "SKILL.md" and path.is_file() for path in ctx.skills.values())
    assert "pre_llm_call" in ctx.hooks

    injected = cast(dict[str, str], ctx.hooks["pre_llm_call"]())
    assert "family_health tools" in injected["context"]
    assert "may never lower" in injected["context"]
    assert "one-time confirmation" in injected["context"]


def test_routing_fixtures_do_not_embed_cyrillic_personal_names():
    # Public test prompts use role labels (patient, child, parent), never a
    # capitalized personal name after a patient-reference preposition.
    pattern = re.compile(r"(?u)\b(?:У|у|Для|для|Про|про)\s+[А-ЯЁ][а-яё]{2,}\b")
    findings = []
    for name in ("test_request_router.py", "test_request_router_natural_language.py"):
        path = ROOT / "tests" / name
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                findings.append(f"{name}:{line_number}")
    assert findings == []
