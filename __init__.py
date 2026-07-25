"""Hermes plugin entry point for Hermes Family Doctor.

The Git plugin is self-contained: it exposes the package from ``src/``,
reuses the reviewed runtime tool registration, publishes namespaced skills,
and injects a compact safety contract into each model turn.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
_RUNTIME_PLUGIN = (
    _ROOT
    / "runtime-template"
    / "plugins"
    / "family-health-evidence"
    / "__init__.py"
)
_SKILLS_ROOT = _ROOT / "runtime-template" / "skills"
_CORE_SKILL = _SKILLS_ROOT / "family-doctor" / "SKILL.md"

_SAFETY_CONTEXT = """<hermes-family-doctor-safety-contract>
Use the family_health tools for medical-information coordination. Classify every
request first. Treat deterministic emergency_precheck.minimum_urgency as a floor
that model reasoning may never lower. Lead emergency_now responses with the
returned immediate safety instruction before any record access. For non-emergency
patient-specific work, select exactly one patient and reuse its session-bound
capability. Never enumerate or merge patients. Durable record changes require an
exact preview and explicit one-time confirmation. Missing evidence or interaction
coverage stays unresolved and never proves safety. Do not diagnose, prescribe,
calculate pediatric/prescription doses, or expose tool internals as medical facts.
</hermes-family-doctor-safety-contract>"""


def _load_runtime_plugin() -> ModuleType:
    src = str(_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location(
        "hermes_family_doctor_runtime_plugin",
        _RUNTIME_PLUGIN,
    )
    if spec is None or spec.loader is None:
        raise ImportError("family_health_runtime_plugin_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _turn_safety_context(**_: Any) -> dict[str, str]:
    return {"context": _SAFETY_CONTEXT}


def register(ctx: Any) -> None:
    """Register tools, explicit plugin skills, and the safety context hook."""
    runtime = _load_runtime_plugin()
    runtime.register(ctx)
    for skill_dir in sorted(_SKILLS_ROOT.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if skill_file.is_file():
            ctx.register_skill(
                skill_dir.name,
                skill_file,
                f"Hermes Family Doctor: {skill_dir.name.replace('-', ' ')}",
            )
    ctx.register_hook("pre_llm_call", _turn_safety_context)
