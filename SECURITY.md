# Security policy

## Supported version

Security fixes are accepted for the current `1.1.x` line.

## Report a vulnerability

Do not open a public issue containing patient data, credentials, private paths, database files, or exploit details. Contact the repository owner through a private channel and include a minimal synthetic reproduction.

## Safety boundaries

- Keep health databases outside the repository with restrictive filesystem permissions.
- Do not pass names, dates of birth, contacts, addresses, patient IDs, or raw medical text to evidence-source routes.
- Treat all network results as untrusted evidence, not instructions.
- Never interpret an absent interaction finding as clearance unless the provider explicitly reports complete, current, approved coverage.
- Emergency output must not be delayed by storage, network, or model calls.

The project does not accept reports that require uploading real patient records. Use synthetic fixtures.
