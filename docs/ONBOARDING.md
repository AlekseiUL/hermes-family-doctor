# Safe onboarding

1. Create a dedicated Python 3.11+ virtual environment outside any live Hermes profile.
2. Install the normal wheel, not an editable checkout, for the release smoke.
3. Run `hermes-family-doctor status`; confirm `clinician_validated` is `false` and interaction coverage is incomplete unless you configured an approved provider.
4. Create a new database path outside the repository and restrict its filesystem permissions.
5. Initialize only that new path with `hermes-family-doctor --db <path> init`.
6. Use synthetic patients first. Verify adult/child isolation, emergency-before-storage behavior, and proposal/confirmation replay rejection.
7. If integrating with Hermes, create a new profile and merge the template manually. Keep raw terminal, file, web, memory, and arbitrary browser tools disabled unless separately reviewed.
8. Back up the profile and database before every upgrade. Do not restart an active gateway until its owner approves a delivery window.

## Data boundary

The application stores the database path you explicitly provide. It does not discover existing family records, import legacy JSON, upload records, or copy a Hermes profile. Evidence queries accept controlled IDs only and must never include patient data.

## Uninstall

```bash
python -m pip uninstall hermes-family-doctor
```

Uninstalling code does not remove health databases. Delete or retain those only under your own retention policy. The project intentionally provides no broad deletion command.

## Rollback

1. Stop using the new profile or package; do not delete it yet.
2. Reinstall the previously recorded wheel version in the dedicated environment.
3. Restore the previous profile/database only from a verified private backup.
4. Run status and synthetic smoke before reconnecting any messaging surface.
5. Keep the failed version and receipt until the incident is understood.
