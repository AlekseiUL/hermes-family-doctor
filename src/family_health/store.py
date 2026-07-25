from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .course_memory import compact_course_summary, normalize_course_tracking
from .security import canonical_json, digest, opaque_token, token_hash, utc_text


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class HealthStore:
    """SQLite persistence. Conversational callers must use FamilyHealthService."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except PermissionError:
            pass
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        try:
            self.path.chmod(0o600)
        except (FileNotFoundError, PermissionError):
            pass

    def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS patients (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            date_of_birth TEXT,
            patient_type TEXT NOT NULL DEFAULT 'adult',
            access_policy_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            category TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL,
            observed_at TEXT,
            last_verified TEXT,
            author TEXT NOT NULL,
            supersedes_id TEXT REFERENCES facts(id),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS facts_patient_idx ON facts(patient_id, category, status);
        CREATE TABLE IF NOT EXISTS pending_writes (
            id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            payload_json TEXT NOT NULL,
            payload_hash TEXT,
            state TEXT NOT NULL DEFAULT 'pending',
            requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS illness_courses (
            id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            complaint_key TEXT NOT NULL,
            display_label TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'recovering', 'resolved')),
            onset_text TEXT,
            opened_at TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            resolved_at TEXT,
            latest_summary_json TEXT NOT NULL,
            update_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            intake_json TEXT NOT NULL,
            urgency_json TEXT NOT NULL,
            course_id TEXT REFERENCES illness_courses(id),
            encounter_kind TEXT NOT NULL DEFAULT 'standalone',
            sequence_no INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capabilities (
            token_hash TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            session_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            operations_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS confirmation_receipts (
            token_hash TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            patient_id TEXT NOT NULL REFERENCES patients(id),
            session_id TEXT NOT NULL,
            pending_id TEXT NOT NULL REFERENCES pending_writes(id),
            payload_hash TEXT NOT NULL,
            preview_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL,
            patient_id TEXT,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            previous_hash TEXT,
            event_hash TEXT
        );
        """
        with self.connect() as db:
            db.executescript(schema)
            self._add_column_if_missing(db, "pending_writes", "payload_hash", "TEXT")
            self._add_column_if_missing(db, "audit_log", "previous_hash", "TEXT")
            self._add_column_if_missing(db, "audit_log", "event_hash", "TEXT")
            self._add_column_if_missing(db, "episodes", "course_id", "TEXT REFERENCES illness_courses(id)")
            self._add_column_if_missing(db, "episodes", "encounter_kind", "TEXT NOT NULL DEFAULT 'standalone'")
            self._add_column_if_missing(db, "episodes", "sequence_no", "INTEGER")
            db.execute(
                "CREATE INDEX IF NOT EXISTS illness_courses_patient_status_idx "
                "ON illness_courses(patient_id, status, last_updated_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS illness_courses_patient_complaint_idx "
                "ON illness_courses(patient_id, complaint_key, status, last_updated_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS episodes_course_idx ON episodes(patient_id, course_id, created_at)"
            )

    @staticmethod
    def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _audit(self, db: sqlite3.Connection, action: str, patient_id: str | None, details: dict[str, Any]) -> None:
        event_id = str(uuid.uuid4())
        created_at = utc_text()
        prior = db.execute(
            "SELECT event_hash FROM audit_log WHERE event_hash IS NOT NULL ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous_hash = prior["event_hash"] if prior else "GENESIS"
        event_hash = digest(canonical_json({
            "event_id": event_id,
            "action": action,
            "patient_id": patient_id,
            "details": details,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }))
        db.execute(
            """INSERT INTO audit_log(
                   event_id, action, patient_id, details_json, created_at, previous_hash, event_hash
               ) VALUES(?,?,?,?,?,?,?)""",
            (event_id, action, patient_id, canonical_json(details), created_at, previous_hash, event_hash),
        )

    def upsert_patient(self, patient: dict[str, Any]) -> None:
        required = {"id", "display_name"}
        if not required <= patient.keys():
            raise ValueError("patient requires id and display_name")
        with self.connect() as db:
            db.execute(
                """INSERT INTO patients(id, display_name, date_of_birth, patient_type, access_policy_json, created_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                     date_of_birth=excluded.date_of_birth, patient_type=excluded.patient_type,
                     access_policy_json=excluded.access_policy_json""",
                (patient["id"], patient["display_name"], patient.get("date_of_birth"),
                 patient.get("patient_type", "adult"), canonical_json(patient.get("access_policy", {})), utc_text()),
            )
            self._audit(db, "patient_upsert", patient["id"], {"fields": sorted(patient.keys())})

    def operator_list_patients(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id, display_name, date_of_birth, patient_type FROM patients ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def issue_capability(
        self,
        *,
        actor: str,
        patient_id: str,
        session_id: str,
        purpose: str,
        operations: Sequence[str],
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> str:
        self._require_patient(patient_id)
        if not actor or not session_id or not purpose or not operations:
            raise ValueError("capability binding fields are required")
        if ttl_seconds <= 0:
            raise ValueError("capability ttl must be positive")
        token = opaque_token()
        current = now or datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute(
                """INSERT INTO capabilities(
                       token_hash, actor, patient_id, session_id, purpose, operations_json,
                       expires_at, created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (token_hash(token), actor, patient_id, session_id, purpose,
                 canonical_json(sorted(set(operations))), utc_text(current + timedelta(seconds=ttl_seconds)), utc_text(current)),
            )
            self._audit(db, "capability_issued", patient_id, {
                "actor": actor, "purpose": purpose, "operations": sorted(set(operations)), "ttl_seconds": ttl_seconds,
            })
        return token

    def validate_capability(
        self,
        token: str,
        *,
        actor: str,
        patient_id: str,
        session_id: str,
        operation: str,
        now: datetime | None = None,
    ) -> None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM capabilities WHERE token_hash=?", (token_hash(token),)).fetchone()
        if row is None:
            raise PermissionError("unknown capability")
        if row["revoked_at"] is not None:
            raise PermissionError("revoked capability")
        if row["actor"] != actor:
            raise PermissionError("capability actor mismatch")
        if row["patient_id"] != patient_id:
            raise PermissionError("capability patient mismatch")
        if row["session_id"] != session_id:
            raise PermissionError("capability session mismatch")
        if operation not in json.loads(row["operations_json"]):
            raise PermissionError("capability operation denied")
        current = now or datetime.now(timezone.utc)
        if _parse_time(row["expires_at"]) <= current:
            raise PermissionError("capability expired")

    def propose_fact(
        self, patient_id: str, payload: dict[str, Any], requested_by: str, *, db: sqlite3.Connection | None = None
    ) -> tuple[str, str]:
        self._require_patient(patient_id)
        required = {"category", "fact_key", "value", "source", "author"}
        if not required <= payload.keys():
            raise ValueError(f"fact payload missing: {sorted(required - payload.keys())}")
        pending_id = str(uuid.uuid4())
        payload_json = canonical_json(payload)
        payload_digest = digest(payload_json)

        def apply(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO pending_writes(
                       id, patient_id, payload_json, payload_hash, requested_by, created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (pending_id, patient_id, payload_json, payload_digest, requested_by, utc_text()),
            )
            self._audit(connection, "fact_proposed", patient_id, {
                "pending_id": pending_id, "category": payload["category"], "status": "pending",
            })

        if db is not None:
            apply(db)
        else:
            with self.connect() as connection:
                apply(connection)
        return pending_id, payload_digest

    def issue_confirmation_receipt(
        self,
        *,
        actor: str,
        patient_id: str,
        session_id: str,
        pending_id: str,
        preview_hash: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(timezone.utc)
        if ttl_seconds <= 0 or not preview_hash:
            raise ValueError("receipt ttl and preview hash are required")
        token = opaque_token()
        with self.connect() as db:
            pending = db.execute("SELECT * FROM pending_writes WHERE id=?", (pending_id,)).fetchone()
            if pending is None:
                raise KeyError("pending write not found")
            if pending["patient_id"] != patient_id:
                raise PermissionError("patient namespace mismatch")
            if pending["state"] != "pending":
                raise ValueError("pending write already resolved")
            payload_digest = pending["payload_hash"] or digest(pending["payload_json"])
            db.execute(
                """INSERT INTO confirmation_receipts(
                       token_hash, actor, patient_id, session_id, pending_id, payload_hash,
                       preview_hash, expires_at, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (token_hash(token), actor, patient_id, session_id, pending_id, payload_digest,
                 preview_hash, utc_text(current + timedelta(seconds=ttl_seconds)), utc_text(current)),
            )
            self._audit(db, "confirmation_receipt_issued", patient_id, {
                "actor": actor, "pending_id": pending_id, "ttl_seconds": ttl_seconds,
            })
        return token

    def confirm_fact_with_receipt(
        self,
        *,
        receipt: str,
        actor: str,
        patient_id: str,
        session_id: str,
        pending_id: str,
        preview_hash: str,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(timezone.utc)
        with self.connect() as db:
            receipt_row = db.execute(
                "SELECT * FROM confirmation_receipts WHERE token_hash=?", (token_hash(receipt),)
            ).fetchone()
            if receipt_row is None:
                raise PermissionError("unknown confirmation receipt")
            if receipt_row["consumed_at"] is not None:
                raise PermissionError("confirmation receipt already consumed")
            if _parse_time(receipt_row["expires_at"]) <= current:
                raise PermissionError("confirmation receipt expired")
            bindings = {
                "actor": actor, "patient_id": patient_id, "session_id": session_id,
                "pending_id": pending_id, "preview_hash": preview_hash,
            }
            for field, expected in bindings.items():
                if receipt_row[field] != expected:
                    raise PermissionError(f"confirmation receipt {field} mismatch")
            pending = db.execute("SELECT * FROM pending_writes WHERE id=?", (pending_id,)).fetchone()
            if pending is None or pending["state"] != "pending":
                raise ValueError("pending write unavailable")
            if pending["patient_id"] != patient_id:
                raise PermissionError("patient namespace mismatch")
            actual_payload_hash = digest(pending["payload_json"])
            if actual_payload_hash != receipt_row["payload_hash"]:
                raise PermissionError("pending payload changed")
            payload = json.loads(pending["payload_json"])
            fact_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO facts(id, patient_id, category, fact_key, value_json, status, source,
                   observed_at, last_verified, author, supersedes_id, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fact_id, patient_id, payload["category"], payload["fact_key"], canonical_json(payload["value"]),
                 payload.get("status", "active"), payload["source"], payload.get("observed_at"),
                 payload.get("last_verified"), payload["author"], payload.get("supersedes_id"), utc_text(current)),
            )
            consumed_at = utc_text(current)
            db.execute(
                "UPDATE confirmation_receipts SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
                (consumed_at, token_hash(receipt)),
            )
            db.execute(
                "UPDATE pending_writes SET state='confirmed', confirmed_at=? WHERE id=? AND state='pending'",
                (consumed_at, pending_id),
            )
            self._audit(db, "fact_confirmed", patient_id, {
                "pending_id": pending_id, "fact_id": fact_id, "actor": actor,
            })
            return fact_id

    def confirm_fact(self, *_args: Any, **_kwargs: Any) -> str:
        raise PermissionError("one-time confirmation receipt required")

    def get_facts(self, patient_id: str, include_inactive: bool = False) -> list[dict[str, Any]]:
        self._require_patient(patient_id)
        query = "SELECT * FROM facts WHERE patient_id=?"
        if not include_inactive:
            query += " AND status='active'"
        query += " ORDER BY category, fact_key, created_at"
        with self.connect() as db:
            rows = db.execute(query, (patient_id,)).fetchall()
            self._audit(db, "patient_facts_read", patient_id, {"count": len(rows)})
            result = []
            for row in rows:
                item = dict(row)
                item["value"] = json.loads(item.pop("value_json"))
                result.append(item)
            return result

    def save_episode(
        self,
        patient_id: str,
        intake: dict[str, Any],
        urgency: dict[str, Any],
        *,
        course_tracking: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._require_patient(patient_id)
        tracking = normalize_course_tracking(course_tracking)
        action = tracking["action"]
        episode_id = str(uuid.uuid4())
        current = now or datetime.now(timezone.utc)
        created_at = utc_text(current)
        course_id: str | None = None
        course_status: str | None = None
        sequence_no: int | None = None
        encounter_kind = "standalone"
        with self.connect() as db:
            if action == "open_new":
                course_id = str(uuid.uuid4())
                course_status = "active"
                sequence_no = 1
                encounter_kind = "initial"
                summary = compact_course_summary(intake, urgency, recorded_at=current)
                onset_text = summary.get("onset")
                db.execute(
                    """INSERT INTO illness_courses(
                           id, patient_id, complaint_key, display_label, status, onset_text,
                           opened_at, last_updated_at, latest_summary_json, update_count
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        course_id, patient_id, tracking["complaint_key"], tracking["display_label"],
                        course_status, str(onset_text) if onset_text is not None else None,
                        created_at, created_at, canonical_json(summary), sequence_no,
                    ),
                )
                self._audit(db, "illness_course_opened", patient_id, {
                    "course_id": course_id, "status": course_status, "update_count": sequence_no,
                })
            elif action in {"continue_existing", "resolve_existing"}:
                course_id = tracking["course_id"]
                row = db.execute(
                    "SELECT * FROM illness_courses WHERE id=? AND patient_id=?",
                    (course_id, patient_id),
                ).fetchone()
                if row is None:
                    raise KeyError("illness course not found")
                if row["status"] == "resolved":
                    raise ValueError("resolved illness course cannot be extended")
                if row["complaint_key"] != tracking["complaint_key"]:
                    raise ValueError("illness course complaint mismatch")
                sequence_no = int(row["update_count"]) + 1
                course_status = tracking["status"]
                encounter_kind = "resolution" if action == "resolve_existing" else "followup"
                resolved_at = created_at if action == "resolve_existing" else None
                summary = compact_course_summary(intake, urgency, recorded_at=current)
                db.execute(
                    """UPDATE illness_courses
                       SET status=?, last_updated_at=?, resolved_at=?, latest_summary_json=?, update_count=?
                       WHERE id=? AND patient_id=?""",
                    (
                        course_status, created_at, resolved_at, canonical_json(summary), sequence_no,
                        course_id, patient_id,
                    ),
                )
                audit_action = "illness_course_resolved" if action == "resolve_existing" else "illness_course_updated"
                self._audit(db, audit_action, patient_id, {
                    "course_id": course_id, "status": course_status, "update_count": sequence_no,
                })
            db.execute(
                """INSERT INTO episodes(
                       id, patient_id, intake_json, urgency_json, course_id,
                       encounter_kind, sequence_no, created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    episode_id, patient_id, canonical_json(intake), canonical_json(urgency),
                    course_id, encounter_kind, sequence_no, created_at,
                ),
            )
            self._audit(db, "episode_created", patient_id, {
                "episode_id": episode_id, "urgency": urgency.get("level"),
                "course_id": course_id, "encounter_kind": encounter_kind,
            })
        return {
            "episode_id": episode_id,
            "course_id": course_id,
            "course_status": course_status,
            "course_update_count": sequence_no,
            "encounter_kind": encounter_kind,
        }

    def get_recent_history(
        self,
        patient_id: str,
        *,
        window_days: int = 90,
        limit: int = 5,
        view: str = "all",
        complaint_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._require_patient(patient_id)
        if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 365:
            raise ValueError("history window must be between 1 and 365 days")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("history limit must be between 1 and 10")
        if view not in {"active", "recent", "all"}:
            raise ValueError("invalid history view")
        current = now or datetime.now(timezone.utc)
        cutoff = utc_text(current - timedelta(days=window_days))

        where = ["patient_id=?"]
        params: list[Any] = [patient_id]
        if view == "active":
            where.append("status IN ('active', 'recovering')")
        elif view == "recent":
            where.append("last_updated_at>=?")
            params.append(cutoff)
        else:
            where.append("(status IN ('active', 'recovering') OR last_updated_at>=?)")
            params.append(cutoff)
        params.append(limit)
        match_order = "CASE WHEN complaint_key=? THEN 0 ELSE 1 END," if complaint_key else ""
        if complaint_key:
            params.insert(-1, complaint_key)
        query = (
            "SELECT * FROM illness_courses WHERE " + " AND ".join(where) +
            f" ORDER BY {match_order} CASE status WHEN 'active' THEN 0 WHEN 'recovering' THEN 1 ELSE 2 END, "
            "last_updated_at DESC LIMIT ?"
        )

        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
            courses: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["latest_summary"] = json.loads(item.pop("latest_summary_json"))
                item["possible_same_problem"] = bool(
                    complaint_key
                    and item["complaint_key"] == complaint_key
                    and item["status"] in {"active", "recovering"}
                )
                courses.append(item)

            unlinked: list[dict[str, Any]] = []
            if view != "active":
                legacy_rows = db.execute(
                    """SELECT id, intake_json, urgency_json, encounter_kind, created_at
                       FROM episodes
                       WHERE patient_id=? AND course_id IS NULL AND created_at>=?
                       ORDER BY created_at DESC LIMIT 3""",
                    (patient_id, cutoff),
                ).fetchall()
                for row in legacy_rows:
                    intake = json.loads(row["intake_json"])
                    urgency = json.loads(row["urgency_json"])
                    unlinked.append({
                        "episode_id": row["id"],
                        "created_at": row["created_at"],
                        "encounter_kind": row["encounter_kind"] or "standalone",
                        "summary": compact_course_summary(intake, urgency, recorded_at=_parse_time(row["created_at"])),
                    })
            possible_match_count = sum(bool(item["possible_same_problem"]) for item in courses)
            self._audit(db, "illness_history_read", patient_id, {
                "view": view,
                "window_days": window_days,
                "course_count": len(courses),
                "unlinked_count": len(unlinked),
                "possible_match_count": possible_match_count,
            })
        return {
            "patient_id": patient_id,
            "view": view,
            "window_days": window_days,
            "courses": courses,
            "course_count": len(courses),
            "unlinked_recent_encounters": unlinked,
            "unlinked_recent_count": len(unlinked),
            "possible_match_count": possible_match_count,
            "same_problem_not_proven_by_similarity": True,
        }

    def operator_audit(self, patient_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if patient_id is None:
                rows = db.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
            else:
                rows = db.execute("SELECT * FROM audit_log WHERE patient_id=? ORDER BY seq", (patient_id,)).fetchall()
            return [dict(row) for row in rows]

    def verify_audit_chain(self) -> bool:
        rows = self.operator_audit()
        previous = "GENESIS"
        for row in rows:
            if row.get("event_hash") is None:
                continue
            details = json.loads(row["details_json"])
            expected = digest(canonical_json({
                "event_id": row["event_id"], "action": row["action"], "patient_id": row["patient_id"],
                "details": details, "created_at": row["created_at"], "previous_hash": row["previous_hash"],
            }))
            if row["previous_hash"] != previous or row["event_hash"] != expected:
                return False
            previous = row["event_hash"]
        return True

    def _require_patient(self, patient_id: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM patients WHERE id=?", (patient_id,)).fetchone()
            if row is None:
                raise KeyError("unknown patient")
