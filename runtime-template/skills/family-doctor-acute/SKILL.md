---
name: family-doctor-acute
description: "Focused acute symptom review, escalation, safety-netting and concise clinical synthesis."
version: 1.0.0
---

# Acute review

1. Run the complete deterministic urgency screen with `course_tracking.action=none` before history access; emergency precedence survives incomplete context. For a non-emergency patient-specific answer, repeat the completed intake once after patient selection with the capability and require `reasoning_allowed=true`.
2. After selecting one patient, read the bounded illness history for the controlled complaint class, then collect onset, course, severity, associated symptoms, relevant measurements, current medicines, allergies and attempts. Open a new course, or link an exact prior course only from explicit continuity/confirmation. Repeat triage after material clarification.
3. Build a one-sentence problem representation. Separate plausible explanations from important possibilities that cannot be excluded; never call either a diagnosis.
4. Give safe actions now, available options, what to monitor, a time boundary for reassessment and concrete return precautions. A blocked medication option is never the whole answer. Do not give false reassurance after `no_rule_triggered`.
5. Retrieve only evidence relevant to the decision. State when the available source is discovery-only or not locally applicable.
