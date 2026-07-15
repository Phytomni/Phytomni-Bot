# Operations, DBA, and GitHub acceptance packet

Owners: Operations, DBA, GitHub administrator, and release owner\
Bot contract: `.codex/handoff/2026-07-15-bot-operations-acceptance-handoff.md`\
Current state: `External Pending`\
Evidence: `Not returned`

This packet covers `RC-OPS-001` through `RC-OPS-003` and `RC-DB-001` through
`RC-DB-002`. It is a handoff only. No command below is authorized by this
repository, and no secret or customer result may enter the returned artifact.

## GitHub configuration (`RC-OPS-001`)

From an authenticated administrator workstation, list repository variable and
secret names with update timestamps, without values. Compare the result with
the sixteen variable names and fifteen secret names in the Operations handoff.
Run the nightly workflow preflight on `release/0.1.3` and return the job URL,
head branch, conclusion, and sanitized missing-name result. A missing or
blank name is a configuration failure, not Bot-code evidence.

## API key rotation (`RC-OPS-002`)

Mint a least-privilege `agents` key for the Web gateway, smoke `/v1/models`
through protected injection, then revoke the old public prefix after the
overlap window. Return only metadata, prefix digests, expiry, revocation
timestamp, and smoke request ID. Keep plaintext key material in the approved
secret manager; never put it in shell history, logs, tickets, or this record.

## GaussDB role and session safety (`RC-DB-001`)

The DBA creates or verifies a non-superuser read-only role with schema usage,
`SELECT` only, and `default_transaction_read_only=on`. Run the authorized
`scripts/gauss_live_probe.py` with the two explicit live flags against an
approved table and column. Return only:

- role privilege booleans and a role identifier digest;
- SQLSTATE `25006` for the Bot read-only transaction;
- SQLSTATE `42501` for the deployment-role denied write;
- pool reuse and `RESET ALL` checks;
- commit, environment class, operator, timestamp, and rollback reference.

The probe must not change rows or expose a DSN, SQL statement, identifier, or
raw driver exception. Any failed or unauthorized probe remains pending.

## Eighteen-query comparison (`RC-DB-002`)

Run `scripts/compare_gauss_queries.py` with an owner-approved old-path or
archived baseline. Return only labels, row counts, sorted columns, hashes,
match totals, commit, and environment class. Expected acceptance is
`matched=18` and `mismatched=0`; missing or unapproved baselines block closure
and must not be edited to hide a mismatch.

## Migration and retirement controls (`RC-OPS-003`)

Use the existing Operations handoff for the ordered procedures:

1. quiesce the API before task DB backup/restore;
1. verify SQLite integrity and immutable backup;
1. stage and validate citation data, then direct/relay smoke;
1. atomically rename the citation table while retaining the previous table;
1. capture configuration backups before nginx/unit retirement;
1. run syntax, listener, and readiness checks before traffic returns.

Return change IDs, counts, smoke request IDs, rollback result, and owner
approval. Do not drop a canonical table, delete a backup, loosen grants, or
remove a legacy listener to make a failed check pass.

## Review rule

Each returned record is reviewed against the acceptance ID and redaction
contract. Local tests and `make scoped` prove only Bot-side safety. A missing
owner transcript leaves the item `External Pending`.
