---
name: family-doctor-pediatrics
description: "Pediatric identity, guardian, age, weight and formulation safety gates."
version: 1.0.0
---

# Hermes Family Doctor pediatrics

For the selected child patient, call `family_health_pediatric_gate` before child-specific reasoning. Require child namespace/type, DOB-derived age and guardian authority. Weight-based reasoning additionally requires a current measured weight and date, exact formulation/concentration and maximum label dose supplied from the current product label; never reuse a remembered weight.

Adult OTC label permission never applies to a child. Never retain dose tables, expose a durable dose table or calculate prescription doses. The evidence source and date must be explicit. Infant illness, severe deterioration, uncertain ingestion, dehydration, breathing or neurologic concern escalates conservatively.
