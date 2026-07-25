# Hermes integration

The repository root is a directly installable Hermes plugin. The `runtime-template/` directory remains a reviewable copy for manual staging. Neither path imports records or restarts a gateway.

## Install path

1. Create a dedicated profile: `hermes profile create familydoctor --no-skills --no-alias`.
2. Configure its model: `hermes --profile familydoctor setup model`.
3. Install and enable the Git plugin: `hermes --profile familydoctor plugins install AlekseiUL/hermes-family-doctor --enable`.
4. Apply the `agent.disabled_toolsets` boundary shown in `runtime-template/config.example.yaml` or the README quick-install command.
5. Confirm `hermes --profile familydoctor plugins list --enabled --no-bundled --plain` lists `hermes-family-doctor`.
6. Start local chat only after the status and synthetic safety smokes below pass. Configure messaging later as a separate change.

The Git plugin exposes its bundled `src/family_health` package, registers the reviewed runtime tools and namespaced skills, and adds a compact urgency-first context hook. The separately exported runtime plugin imports an installed wheel and still fails closed when that package is unavailable.

## Recommended boundary

Keep the `family_health` plugin available while disabling terminal, file, memory, raw web, browser, code execution, messaging, and cross-agent routing toolsets unless each boundary has been separately reviewed. Session identity is derived from authenticated Hermes session context and hashed before it reaches the health store.

## Verification

- `hermes --profile familydoctor plugins list --enabled --no-bundled --plain` lists the enabled plugin;
- a local Hermes turn can call `family_health_status` and reports `clinician_validated: false`;
- plugin registration exposes the documented `family_health` tools and namespaced skills;
- a synthetic emergency intake succeeds when the database path is absent;
- patient reads fail without a matching session-bound capability;
- write confirmation fails on replay;
- evidence searches reject raw query and patient fields.

Hermes configuration syntax can change. Verify the example against the current Hermes Agent documentation before activation.

## Uninstall and rollback

Disable or remove the plugin without deleting the private health database:

```bash
hermes --profile familydoctor plugins remove hermes-family-doctor
```

If a prior plugin revision was backed up, reinstall that pinned revision and repeat the local verification. Profile deletion and medical-record deletion are separate destructive operations and are never performed by this repository.
