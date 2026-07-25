---
name: family-doctor-longitudinal
description: "Longitudinal family record reconciliation, follow-up preparation and uncertainty tracking."
version: 1.0.0
---

# Longitudinal review

1. Select one patient and read only categories needed for the current follow-up. Keep shared family history explicitly separate.
2. After urgency screening, use `family_health_recent_history` for a current/repeated complaint. Read at most five courses over 90 days; active/recovering courses come first. Treat `unlinked_recent_encounters` as clues, not established continuity.
3. Continue only the exact course ID when the user explicitly refers to the earlier illness or confirms your one short question. Similar complaint keys alone do not prove one episode. Mark `recovering` when improving and `resolved` only after explicit recovery.
4. Reconcile medicines, allergies, conditions, measurements and the course timeline. Distinguish verified facts, patient-reported episode summaries, contradictions and missing confirmation.
5. The compact course journal is allowed; durable fact corrections still require the proposal/receipt path. Preserve dates and never call a course label a diagnosis.
6. Keep output compact: current picture, change since last contact, open questions and next checkpoint. Never expose `course_id`, UUIDs or storage/status fields. An opened course is new; describe continuity only after the runtime confirms continuation or resolution.
