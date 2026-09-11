# Continuity Status

Current branch: `scrappy/continuity-capsule-v1`

## VERIFIED IN CI

- provider-neutral capsule construction;
- public-safe mode omits raw episodes;
- SHA-256 tamper detection;
- AES-256-GCM private archive round trip;
- wrong-passphrase/authentication failure;
- embedded canonical Vault bundle round trip;
- safe Vault tar transport and traversal/link rejection;
- local ChatGPT JSON export active-chain parsing and credential masking;
- API importability and `scrappy-continuity` CLI installation.

## IMPLEMENTED, NOT DEPLOYED

- authenticated live Vault archive export endpoint;
- authenticated archive import endpoint with dry-run default;
- local encrypted backup command;
- optional local ChatGPT export inclusion;
- verified-capsule restore command with explicit `--apply`;
- provider-neutral bootstrap and restore/eval artifacts.

## NOT YET VERIFIED END TO END

- a real backup of Karnveer's current production Vault into an operator-owned encrypted file;
- ingestion of the real account conversation export;
- an independent secondary and offline encrypted copy;
- clean-environment restore to another Vault deployment;
- fresh-model/provider continuity eval pass.

Nothing in this status claims consciousness transfer or access to history that was never explicitly exported.
