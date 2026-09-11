# Continuity Next Milestones

1. Keep this branch green and reviewed; do not merge/deploy on assumption.
2. After explicit operator approval, deploy archive export/import endpoints.
3. On the operator's Mac, create and verify the first real encrypted production Vault capsule.
4. Obtain the account's official conversation export and ingest `conversations.json` locally into a new encrypted capsule.
5. Copy encrypted bytes to an independent private destination and an offline destination; verify SHA-256 after each copy.
6. Restore to a clean disposable Vault environment using the default dry run, then explicit apply.
7. Bootstrap a fresh model/provider with no original account memory and run continuity evals.
8. Close Issue #30 only when the recovery drill passes end to end.
