# Offline verification boundary

Read-only CI permissions and versioned synthetic source/privacy fixture contract.

## Reproduce

`python -m pytest`

Fixture versioning protects source-shape and privacy regressions, not clinical validity. Changing fixture files requires a reviewed manifest digest/version update. No medical data or live source lookup is used.

## Rollback

Review the commit diff and use a new checkout of the pre-change commit for source rollback. Keep user data and live installations untouched. A source rollback is not permission to restore unsafe network exposure or bypass approval.
