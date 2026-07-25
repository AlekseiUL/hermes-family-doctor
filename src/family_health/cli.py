from __future__ import annotations

import argparse
from importlib import resources
import json
import os
from pathlib import Path
import shutil
from typing import Any

from .service import CONVERSATIONAL_OPERATIONS, FamilyHealthService
from .store import HealthStore


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _binding_arguments(command: argparse.ArgumentParser, *, capability: bool = True) -> None:
    if capability:
        command.add_argument("--capability", required=True)
    command.add_argument("--actor", required=True)
    command.add_argument("--session", required=True)


def _copy_resource_tree(source: Any, target: Path) -> None:
    target.mkdir()
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            _copy_resource_tree(child, destination)
        else:
            destination.write_bytes(child.read_bytes())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-family-doctor")
    parser.add_argument("--db", default=os.environ.get("FAMILY_HEALTH_DB"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    export = commands.add_parser("export-runtime-template")
    export.add_argument("--target", type=Path, required=True)

    patients = commands.add_parser("patients", help="operator-only namespace inspection")
    patients.add_argument("--operator", action="store_true")

    legacy = commands.add_parser("import-json", help="disabled legacy migration surface")
    legacy.add_argument("--file", required=True)

    select = commands.add_parser("select-patient")
    select.add_argument("--patient", required=True)
    select.add_argument("--purpose", required=True)
    select.add_argument("--operations", nargs="+", choices=sorted(CONVERSATIONAL_OPERATIONS), required=True)
    _binding_arguments(select, capability=False)

    facts = commands.add_parser("facts")
    facts.add_argument("--patient", required=True)
    _binding_arguments(facts)

    intake = commands.add_parser("intake")
    intake.add_argument("--file", required=True)
    intake.add_argument("--capability")
    intake.add_argument("--actor", required=True)
    intake.add_argument("--session", required=True)

    propose = commands.add_parser("propose-fact")
    propose.add_argument("--patient", required=True)
    propose.add_argument("--file", required=True)
    _binding_arguments(propose)

    issue = commands.add_parser("issue-confirmation-receipt")
    issue.add_argument("--patient", required=True)
    issue.add_argument("--pending", required=True)
    issue.add_argument("--preview-hash", required=True)
    _binding_arguments(issue)

    confirm = commands.add_parser("confirm-fact")
    confirm.add_argument("--patient", required=True)
    confirm.add_argument("--pending", required=True)
    confirm.add_argument("--preview-hash", required=True)
    confirm.add_argument("--receipt", required=True)
    _binding_arguments(confirm)

    audit = commands.add_parser("audit")
    audit.add_argument("--patient")
    audit.add_argument("--operator", action="store_true")
    audit.add_argument("--capability")
    audit.add_argument("--actor")
    audit.add_argument("--session")
    commands.add_parser("status")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "status":
        emit(FamilyHealthService(HealthStore(":memory:")).status())
        return 0
    if args.command == "export-runtime-template":
        requested_target = args.target.expanduser()
        try:
            resolved_parent = requested_target.parent.resolve(strict=True)
        except OSError:
            resolved_parent = None
        if (
            requested_target.exists()
            or requested_target.is_symlink()
            or resolved_parent is None
            or not resolved_parent.is_dir()
        ):
            emit({"ok": False, "error": "target_must_be_new_with_existing_parent"})
            return 2
        target = resolved_parent / requested_target.name
        source = resources.files("family_health").joinpath("resources", "runtime-template")
        try:
            _copy_resource_tree(source, target)
        except (OSError, TypeError):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            emit({"ok": False, "error": "runtime_template_export_failed"})
            return 2
        emit({"ok": True, "target": target.name, "absolute_path_serialized": False})
        return 0
    if not args.db:
        emit({"ok": False, "error": "database_path_required", "hint": "set --db or FAMILY_HEALTH_DB"})
        return 2

    store = HealthStore(args.db)
    service = FamilyHealthService(store)
    try:
        if args.command == "intake":
            # The service performs triage before any store access or initialization.
            emit(service.evaluate_intake(
                args.capability,
                actor=args.actor,
                session_id=args.session,
                structured_intake=load_json(args.file),
            ))
            return 0

        store.init()
        if args.command == "init":
            emit({"ok": True, "network_side_effects": False})
        elif args.command == "patients":
            if not args.operator:
                raise PermissionError("operator flag required")
            emit({"patients": store.operator_list_patients()})
        elif args.command == "import-json":
            emit({"ok": False, "error": "legacy_import_disabled", "write_applied": False})
            return 2
        elif args.command == "select-patient":
            emit({"capability": service.select_patient(
                patient_id=args.patient, actor=args.actor, session_id=args.session,
                purpose=args.purpose, operations=args.operations,
            )})
        elif args.command == "facts":
            emit({"patient_id": args.patient, "facts": service.get_patient_summary(
                args.capability, actor=args.actor, patient_id=args.patient, session_id=args.session,
            )})
        elif args.command == "propose-fact":
            emit(service.propose_fact(
                args.capability, actor=args.actor, patient_id=args.patient, session_id=args.session,
                payload=load_json(args.file),
            ))
        elif args.command == "issue-confirmation-receipt":
            emit({"receipt": service.issue_confirmation_receipt(
                args.capability, actor=args.actor, patient_id=args.patient, session_id=args.session,
                pending_id=args.pending, preview_hash=args.preview_hash,
            )})
        elif args.command == "confirm-fact":
            fact_id = service.confirm_fact(
                args.capability, receipt=args.receipt, actor=args.actor, patient_id=args.patient,
                session_id=args.session, pending_id=args.pending, preview_hash=args.preview_hash,
            )
            emit({"ok": True, "state": "confirmed", "fact_id": fact_id, "write_applied": True})
        elif args.command == "audit":
            if args.operator:
                emit({"events": store.operator_audit(args.patient)})
            elif args.patient and args.capability and args.actor and args.session:
                emit({"events": service.get_patient_audit(
                    args.capability, actor=args.actor, patient_id=args.patient, session_id=args.session,
                )})
            else:
                raise PermissionError("scoped capability or operator flag required")
    except (KeyError, ValueError, PermissionError, RuntimeError, json.JSONDecodeError):
        emit({"ok": False, "error": "request_rejected"})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())