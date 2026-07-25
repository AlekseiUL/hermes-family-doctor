---
name: family-doctor-records
description: "Patient isolation, capability, receipt and document controls."
version: 1.0.0
---

# Hermes Family Doctor records

Patient ID is not authorization. Use a short-lived opaque actor/patient/session/purpose/operation capability and request only the categories needed for this question. Never enumerate patients, request all facts by default or merge `shared-family-history` into an individual's facts without a separate label.

For a durable card correction, select `record_update`, call `family_health_propose_card_update`, show the human-readable preview, and call `family_health_confirm_card_update` only after explicit confirmation. Its payload/preview-bound receipt is one-time and atomic. The separate illness-course journal may open a compact new current problem under the owner's standing opt-in; continuation/resolution requires an exact patient-scoped course ID and explicit basis. Never merge by similarity or simulate writes through terminal/file tools. Documents require a separately reviewed preview/retention flow. No patient data enters public source queries, Git, Kanban or general memory.

Treat `verification_status=unknown|invalid` as historical or unconfirmed, not current truth. If `current_confirmation_required=true`, reconfirm the relevant medicine or allergy before patient-specific reasoning. Ask only about facts relevant to the current request and at most one relevant `next_reconfirmation_candidate` per conversation.

History reads are bounded to the selected patient. Course summaries are patient reports, not verified diagnoses. Old `unlinked_recent_encounters` remain unlinked unless the user confirms continuity; never rewrite history retroactively.
