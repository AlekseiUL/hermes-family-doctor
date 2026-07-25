---
name: family-doctor-medications
description: "Medication context and high-risk stop contract."
version: 1.0.0
---

# Hermes Family Doctor medications

Before patient-specific review call `family_health_medication_gate`. Set `use_case` to `new_otc_self_care`, `current_medication_review` or `prescription_review`. Use the schema's exact keys, including controlled `candidate_ingredient_id`, every other active ingredient in `current_ingredient_ids`, `interaction_identity_status` and `supplements_present`; preserve duplicate IDs. Use explicit `not_taking` for confirmed no recent dose. The outer `patient_id` is authoritative and need not be duplicated inside context. Current or prescription review also requires `prescriber_instruction`.

For a new adult OTC option, continue only when `exact_otc_label_directions_allowed=true`. Search the controlled ingredient once with `route=labels`, choose at most two plausible records, and fetch one matching DailyMed SETID at a time. Stop when product, active ingredient, form and concentration match. Directions may be reported only when the exact record also has `nonprescription_label_confirmed=true`, `directions_source_scope=exact_product_label` and `dose_sections_included=true`; name the product/concentration and label date. These are label directions, not a calculated dose or prescription.

The gate itself performs exact duplicate detection and pairwise DDI coverage; never supply or invent a coverage-complete flag. The public DDInter provider is signal-only: a finding blocks pending current verification, and no finding never proves compatibility. A duplicate, provider finding, unmapped ingredient, supplement, unknown list or incomplete pair coverage blocks directions and routes to a clinician/pharmacist. `complete_drug_drug_pair_check=true` applies only to the normalized requested drug pairs and never clears allergy, disease, dose, timing, food or supplement risks. Still provide safe non-drug care, the exact missing item and return precautions. Never start, stop or change a prescription and never say overall medication safety is fully cleared.
