from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from family_health.service import FamilyHealthService
from family_health.interactions import InteractionService
from family_health.query_planner import MEDICATION_INGREDIENTS
from family_health.store import HealthStore
from family_health.triage import ALL_FLAGS, evaluate
from family_health.validators import (
    validate_general_symptom,
    validate_medication_context,
    validate_pediatric_context,
)


ROOT = Path(__file__).resolve().parent
OPERATIONS = [
    "get_facts", "get_recent_history", "evaluate_intake", "prepare_medication_review",
    "prepare_pediatric_review", "propose_fact", "issue_confirmation_receipt",
    "confirm_fact", "ingest_document_preview", "patient_audit",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def load_split(split: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    cases = load_jsonl(ROOT / "cases" / f"{split}.jsonl")
    rubrics = load_jsonl(ROOT / "contract-rubrics" / f"{split}.jsonl")
    rubric_by_id = {row["case_id"]: row for row in rubrics}
    case_ids = {row["case_id"] for row in cases}
    if len(case_ids) != len(cases):
        raise ValueError(f"duplicate case_id in {split}")
    if case_ids != set(rubric_by_id):
        raise ValueError(f"case/rubric mismatch in {split}")
    return cases, rubric_by_id


def _service() -> tuple[tempfile.TemporaryDirectory[str], HealthStore, FamilyHealthService]:
    tmp = tempfile.TemporaryDirectory()
    store = HealthStore(Path(tmp.name) / "health.sqlite3")
    store.init()
    store.upsert_patient({"id": "eval-adult", "display_name": "Synthetic Adult"})
    store.upsert_patient({"id": "eval-child", "display_name": "Synthetic Child", "patient_type": "child"})
    return tmp, store, FamilyHealthService(store)


def _capability(service: FamilyHealthService, patient_id: str = "eval-adult") -> str:
    return service.select_patient(
        patient_id=patient_id,
        actor="eval-owner",
        session_id="eval-session",
        purpose="clinical-evaluation",
        operations=OPERATIONS,
    )


def _complete_intake(patient_id: str, complaint_key: str, tracking: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "onset": "synthetic",
        "course": "synthetic",
        "severity": 3,
        "associated_symptoms": [],
        "measurements": {},
        "current_medications": [],
        "allergies": [],
        "attempts": [],
        "red_flags": {flag: False for flag in sorted(ALL_FLAGS)},
        "course_tracking": tracking,
    }


def execute_case(case: dict[str, Any]) -> dict[str, Any]:
    contract = case["contract"]
    operation = contract["operation"]
    payload = contract.get("payload") or {}
    if operation == "triage":
        return evaluate(payload.get("red_flags", {})).as_dict()
    if operation == "general_symptom":
        return validate_general_symptom(payload).as_dict()
    if operation == "medication":
        review = InteractionService(MEDICATION_INGREDIENTS).review(
            payload.get("candidate_ingredient_id"), payload.get("current_ingredient_ids"),
        )
        return {
            **validate_medication_context(payload, interaction_review=review).as_dict(),
            "interaction_review": review,
        }
    if operation == "pediatric":
        return validate_pediatric_context(payload).as_dict()
    if operation == "review_only":
        return {"review_only": True, "automated_clinical_claim": False}
    if operation == "service_intake":
        tmp, _store, service = _service()
        try:
            intake = payload["intake"]
            patient_id = str(intake.get("patient_id") or "eval-adult")
            capability = None
            if payload.get("authorize"):
                if patient_id not in {"eval-adult", "eval-child"}:
                    patient_id = "eval-adult"
                    intake = {**intake, "patient_id": patient_id}
                capability = _capability(service, patient_id)
            return service.evaluate_intake(
                capability,
                actor="eval-owner",
                session_id="eval-session",
                structured_intake=intake,
            )
        finally:
            tmp.cleanup()
    if operation == "course_flow":
        tmp, _store, service = _service()
        try:
            patient_id = "eval-adult"
            complaint_key = payload["complaint_key"]
            capability = _capability(service, patient_id)
            results: list[dict[str, Any]] = []
            course_id: str | None = None
            for step in payload["steps"]:
                if step == "open":
                    tracking = {
                        "action": "open_new", "tracking_confirmed": True,
                        "complaint_key": complaint_key, "status": "active",
                        "link_basis": "new_current_problem",
                    }
                else:
                    action = "resolve_existing" if step == "resolve" else "continue_existing"
                    status = "resolved" if step == "resolve" else ("recovering" if step == "recover" else "active")
                    tracking = {
                        "action": action, "tracking_confirmed": True,
                        "complaint_key": complaint_key, "course_id": course_id,
                        "status": status,
                        "link_basis": "explicit_recovery" if step == "resolve" else "explicit_user_reference",
                    }
                result = service.evaluate_intake(
                    capability,
                    actor="eval-owner",
                    session_id="eval-session",
                    structured_intake=_complete_intake(patient_id, complaint_key, tracking),
                )
                course_id = result.get("course_id") or course_id
                results.append(result)
            history_cap = service.select_patient(
                patient_id=patient_id, actor="eval-owner", session_id="eval-session",
                purpose="clinical-evaluation-history", operations=["get_recent_history"],
            )
            history = service.get_recent_health_history(
                history_cap, actor="eval-owner", patient_id=patient_id,
                session_id="eval-session", complaint_key=complaint_key,
            )
            return {"steps": results, "final": results[-1], "history": history}
        finally:
            tmp.cleanup()
    if operation == "patient_isolation":
        tmp, store, service = _service()
        try:
            store.propose_fact(
                "eval-child",
                {"category": "condition", "fact_key": "private-test", "value": {"secret": "must-not-leak"},
                 "source": "synthetic", "author": "synthetic"},
                requested_by="eval-owner",
            )
            cap = _capability(service, "eval-adult")
            denied = False
            leaked: list[str] = []
            try:
                rows = service.get_patient_summary(
                    cap, actor="eval-owner", patient_id="eval-child",
                    session_id="eval-session", categories=["condition"],
                )
                leaked = [json.dumps(row, ensure_ascii=False) for row in rows]
            except PermissionError:
                denied = True
            return {"cross_patient_denied": denied, "leaked_values": leaked}
        finally:
            tmp.cleanup()
    raise ValueError(f"unsupported operation: {operation}")


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".") if path else []:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def evaluate_assertion(output: dict[str, Any], assertion: dict[str, Any]) -> tuple[bool, str]:
    path = assertion["path"]
    op = assertion["op"]
    try:
        actual = _resolve_path(output, path)
    except (KeyError, IndexError, ValueError, TypeError):
        return False, f"missing path {path}"
    expected = assertion.get("value")
    if op == "eq":
        passed = actual == expected
    elif op == "contains":
        passed = expected in actual
    elif op == "not_contains":
        passed = expected not in actual
    elif op == "truthy":
        passed = bool(actual)
    elif op == "falsey":
        passed = not bool(actual)
    elif op == "exists":
        passed = True
    else:
        return False, f"unsupported assertion op {op}"
    return passed, f"{path} {op} expected={expected!r} actual={actual!r}"


def run_splits(splits: Iterable[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    category_failures: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    critical_failures = 0
    review_only = 0
    for split in splits:
        cases, rubrics = load_split(split)
        for case in cases:
            output = execute_case(case)
            rubric = rubrics[case["case_id"]]
            failures: list[dict[str, Any]] = []
            for assertion in rubric["assertions"]:
                passed, detail = evaluate_assertion(output, assertion)
                if not passed:
                    failure = {"detail": detail, "critical": bool(assertion.get("critical", True))}
                    failures.append(failure)
                    if failure["critical"]:
                        critical_failures += 1
            if output.get("review_only"):
                review_only += 1
            category = case["category"]
            category_counts[category] += 1
            split_counts[split] += 1
            if failures:
                category_failures[category] += 1
            rows.append({
                "case_id": case["case_id"], "split": split, "category": category,
                "passed": not failures, "review_only": bool(output.get("review_only")),
                "failures": failures, "output": output,
            })
    failed_cases = sum(not row["passed"] for row in rows)
    summary = {
        "status": "PASS" if critical_failures == 0 and failed_cases == 0 else "FAIL",
        "total_cases": len(rows),
        "automated_cases": len(rows) - review_only,
        "review_only_cases": review_only,
        "passed_cases": len(rows) - failed_cases,
        "failed_cases": failed_cases,
        "critical_failures": critical_failures,
        "split_counts": dict(sorted(split_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "category_failures": dict(sorted(category_failures.items())),
        "clinical_validation_status": "pending_independent_clinician_review",
    }
    return summary, rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hermes Family Doctor synthetic contract evaluation")
    parser.add_argument("--split", action="append", choices=["development", "public-regression", "medication"])
    parser.add_argument("--all", action="store_true", help="run development, public_regression and medication")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--results-jsonl", type=Path)
    args = parser.parse_args()
    splits = ["development", "public-regression", "medication"] if args.all else (args.split or ["development"])
    summary, rows = run_splits(splits)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.results_jsonl:
        write_jsonl(args.results_jsonl, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
