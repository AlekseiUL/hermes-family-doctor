# Optional interaction provider

The base package uses `NoInteractionProvider` and fails closed. To use an owner-approved SQLite signal source, set:

```bash
export FAMILY_HEALTH_DDI_SQLITE=/private/path/interaction-signals.sqlite3
```

The file is opened read-only. It must contain:

- `metadata(key TEXT PRIMARY KEY, value TEXT)`;
- `interactions(ingredient_a, ingredient_b, severity, evidence_level, mechanism, management, source_id, source_record_id)`.

Required metadata includes `schema_version=family-doctor-ddi.v1`, `license_status=owner_approved`, `provider_mode`, `dataset_version`, and JSON `supported_ingredient_ids`. Complete negative clearance additionally requires `coverage_complete=true` and a non-expired `valid_until`. A signal-only provider may expose positive findings only when `finding_use_allowed=true`; missing rows remain unresolved.

No interaction result clears overall medication safety. Food, supplement, allergy, disease, dose, timing, pregnancy, renal, hepatic, and formulation risks require separate review.

DDInter data and conversion code are not distributed. A user who independently prepares a DDInter-derived database must comply with the current upstream non-commercial/share-alike and attribution terms. This repository's MIT license does not relicense that data.
