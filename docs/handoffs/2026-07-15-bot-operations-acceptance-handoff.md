# Operations acceptance handoff: Bot 0.1.3

**Date:** 2026-07-15\
**Release:** `0.1.3` on `release/0.1.3`\
**Bot owner:** Phytomni-Bot maintainer\
**External owners:** Operations, DBA, GitHub administrator, and release owner\
**Overall status:** External Pending\
**Evidence:** Not returned

This is an operator-facing procedure package. It supplies commands, expected
sanitized observations, rollback boundaries, and evidence fields; it does not
execute a production action. Replace every angle-bracket or dollar-prefixed
placeholder with a value approved for the target environment. Never place a
secret, DSN, customer row, SQL result, or raw provider response in the
evidence bundle.

## Safety gates and ownership

No production command is authorized by this document alone. The owner must
record a change ticket, maintenance window, operator, target environment,
release identifier, and rollback owner before starting. Stop immediately when
an expected precondition is absent.

| Rule               | Required behavior                                                                                                                                     |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Service quiescence | Stop `phytomni-api` before task-database rollback preparation, SQLite restore, or legacy-service retirement.                                          |
| Version rollback   | Install the prior wheel or image first; only then restore its environment file. Restart and run readiness checks before traffic returns.              |
| Key handling       | A newly minted plaintext API key is captured once into the approved secret manager and is never copied to evidence, logs, shell history, or a ticket. |
| Database writes    | Gauss role creation and citation cutover require DBA approval; staged validation must pass before the canonical rename.                               |
| Live evidence      | `PHYTOMNI_RUN_INTEGRATION=1` and `PHYTOMNI_ALLOW_NETWORK=1` are explicit authorization gates, not a routine CI setting.                               |
| External status    | Every row remains `External Pending` with `Evidence: Not returned` until an owner returns sanitized proof.                                            |

The local Bot gate, unit tests, and offline E2E collection cannot close a
production, database, GitHub, or live-backend item.

## GitHub Actions configuration

The nightly workflow expects sixteen repository variables and fifteen
repository secrets. Verify names and update timestamps without printing
values:

### Sixteen repository variables

`TOKEN_URL`, `RETRIEVE_URL`, `RERANK_URL`, `SPA_FAQ_URL`, `DATABASE_URL`,
`ANALYSIS_URL`, `OBS_SERVER`, `REPO_ID`, `REPO_ID_DICT`, `WORKSPACE_ID`,
`SUBJECT_ID`, `DATA_REPO_ID`, `TOOL_REPO_ID`, `PROTOCOL_REPO_ID`,
`SPA_REPO_ID`, `APP_ID`.

### Fifteen repository secrets

`DOMAIN_NAME`, `USER_NAME`, `USER_PASSWORD`, `ACCESS_KEY_ID`,
`SECRET_ACCESS_KEY`, `BASE_URL`, `MODEL_ID`, `API_KEY`, `CODER_URL`,
`CODER_MODEL`, `CODER_API_KEY`, `GAUSS_DSN`, `EMBED_URL`, `EMBED_MODEL`,
`EMBED_API_KEY`.

Run from an authenticated GitHub administrator workstation:

```bash
export GITHUB_REPO=<owner>/<repo>
gh variable list --repo "$GITHUB_REPO" --json name,updatedAt
gh secret list --repo "$GITHUB_REPO" --json name,updatedAt
```

Expected sanitized result: every name above appears exactly once, values are
never shown, and no secret is copied to the terminal transcript. Correct a
missing or blank item in GitHub Settings, then run the workflow's own
configuration preflight:

```bash
gh workflow run e2e-nightly.yml --repo "$GITHUB_REPO" --ref release/0.1.3
gh run list --repo "$GITHUB_REPO" --workflow e2e-nightly.yml --limit 1 \
  --json databaseId,status,conclusion,headBranch
```

Do not attach the run's environment block or uploaded customer output. A
failed preflight is a configuration issue, not evidence that the Bot code is
broken.

**Owner:** GitHub administrator\
**Rollback:** restore the previous secret/variable version from the approved
secret manager or disable the workflow dispatch; do not delete a working
secret during diagnosis.\
**Status:** External Pending\
**Evidence:** Not returned

## Production API key lifecycle

Mint one least-privilege key for the Web gateway. The command prints the
plaintext once; capture it directly into the approved secret manager using
the organization's protected input flow. Do not use `tee`, shell tracing, or
an unredacted CI variable.

```bash
phytomni-api-key create --user-id web --name production-web --expires-days 90 --scope agents
```

Expected sanitized result: a metadata record for user `web`, scope exactly
`agents`, a ninety-day expiry, and one public key prefix. Verify metadata only:

```bash
phytomni-api-key list --user-id web
```

The gateway must use the key through its protected environment injection. A
rotation is additive: mint the replacement, deploy and smoke it, then revoke
the prior public prefix:

```bash
phytomni-api-key revoke --prefix "$OLD_WEB_KEY_PREFIX"
phytomni-api-key list --user-id web
```

Expected sanitized result: the replacement remains active, the old prefix is
marked revoked or absent from active listings, and no plaintext value appears
in output. Schedule the next rotation before the ninety-day expiry and retain
the revocation timestamp in the secret-manager audit, not in this repository.

**Owner:** Operations / Web release owner\
**Rollback:** keep the prior key until the replacement passes `/v1/models`,
then restore the prior protected value and revoke only the failed replacement.\
**Status:** External Pending\
**Evidence:** Not returned

## GaussDB least-privilege role and live safety probes

The deployment role must be read-only at three independent layers: Bot parse
policy, `transaction(readonly=True)`, and database grants/default
transaction mode. DBA actions below use an interactive administrative
connection; do not place the administrative DSN or password in evidence.

### Role creation and grants

The DBA should create a dedicated role with a protected password, grant only
database connect, schema usage, and `SELECT` on the required schemas/tables,
and set `default_transaction_read_only=on`. The exact role/database names
are deployment values; verify them through the approved secret manager.

Expected sanitized result:

- role exists and is not a superuser, owner, or role with create/write grants;
- connect and schema-usage checks pass;
- table privileges contain `SELECT` only;
- `default_transaction_read_only` is `on` for the Bot role.

Do not record `\du`, `\dp`, DSNs, or full catalog rows. Record only the
allowlisted privilege booleans and the role identifier digest.

### Authorized Bot safety probe

Approve one harmless existing table and a nullable column before running:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run python scripts/gauss_live_probe.py \
  --table <approved_table> \
  --column <approved_column> \
  --environment-class production \
  --output e2e/output/gauss_live_probe.json
```

Expected sanitized JSON contains only `commit`, `environment_class`,
`checks`, and `sqlstates`. All checks must be `pass`, with SQLSTATE `25006`
for the Bot read-only transaction and `42501` for the deployment-role denied
write. The pool check must prove a second borrower does not retain the first
borrower's session marker. Exit `2` means authorization/input validation,
exit `1` means a failed check or evidence write, and exit `0` means all
checks passed.

The probe is not a row-writing test: the attempted insert selects from
`WHERE FALSE` and is rolled back before SQLSTATE inspection. Do not change
the approved table, column, role, or flags to make a failed probe pass.

**Owner:** DBA and Operations\
**Rollback:** revoke the newly created role and restore the previous protected
DSN only after the service is stopped; do not loosen grants to bypass a
failure.\
**Status:** External Pending\
**Evidence:** Not returned

## Task database backup and rollback preparation

Set absolute paths and stop every API worker before touching the local SQLite
registry. The command below performs the repository's child-aware rollback
preparation and refuses persisted nonterminal work unless the operator
explicitly acknowledges that it will be failed.

```bash
export TASK_DB=<absolute-path-to-server_tasks.db>
export BACKUP_DIR=<approved-local-backup-directory>
sudo systemctl stop phytomni-api
sqlite3 "$TASK_DB" "PRAGMA integrity_check;"
mkdir -p "$BACKUP_DIR"
sqlite3 "$TASK_DB" ".backup '$BACKUP_DIR/server_tasks.before-rollback.sqlite'"
phytomni-task-db prepare-deep-genome-rollback --db "$TASK_DB"
```

Expected sanitized result: integrity check returns `ok`; the CLI prints the
database and backup paths plus counts for deleted remote-task/section rows;
nonterminal runs are zero unless the explicit acknowledgement below was
approved. Verify the backup independently:

```bash
sqlite3 "$BACKUP_DIR/server_tasks.before-rollback.sqlite" \
  "PRAGMA integrity_check;"
```

If a stop-window owner has approved failing persisted work, rerun the final
command with `--mark-nonterminal-failed`. Record the count, reason, and
approval id. Never run rollback preparation while the service is accepting
traffic.

### Restore and prior-binary ordering

For a release rollback, install the prior wheel or image first, stop the
service, restore the SQLite backup, restore the prior environment file, and
start the prior binary. The order is deliberate: restoring old environment
keys while the new binary is still installed can create a false-success
startup.

```bash
sudo systemctl stop phytomni-api
sudo <approved-package-manager> install <prior-wheel-or-image>
sudo cp "$BACKUP_DIR/server_tasks.before-rollback.sqlite" "$TASK_DB"
sudo cp <prior-environment-file> <active-environment-file>
sudo systemctl start phytomni-api
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS -H "Authorization: Bearer $PHYTOMNI_API_KEY" \
  http://127.0.0.1:8080/v1/models
```

Expected sanitized result: the installed version is the prior release,
`/readyz` is `200`, `/v1/models` is authenticated, and the restored database
passes `PRAGMA integrity_check`. Do not reintroduce retired BI compatibility
settings or point the prior binary at a new-schema-only store.

**Owner:** Operations / release owner\
**Rollback:** keep the backup immutable; if readiness fails, stop the service,
restore the known-good prior backup and prior package, and reopen the change
ticket.\
**Status:** External Pending\
**Evidence:** Not returned

## Eighteen-query direct-Gauss comparison

An owner-approved baseline manifest is required. The Bot runner consumes the
locked eighteen-query corpus and writes only counts, sorted columns, and
SHA-256 fingerprints; it never writes SQL or result rows to evidence.

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run python scripts/compare_gauss_queries.py \
  --baseline <owner-approved-baseline.json> \
  --environment-class production \
  --output e2e/output/gauss_query_comparison.json
```

Expected sanitized result: exit `0` with `matched=18` and
`mismatched=0`. Exit `2` means the owner baseline is missing/invalid or live
authorization is absent; exit `1` means a query mismatch, query failure, or
evidence-write failure. A mismatch blocks cutover and is not fixed by editing
the baseline after the fact. If the old service no longer exists, an owner
may supply an archived golden with provenance, corpus labels, counts,
columns, and hashes; it remains External Pending until approved.

**Owner:** DBA / Operations\
**Rollback:** stop the cutover and keep the prior service/binary; do not
rewrite the manifest to hide a mismatch.\
**Status:** External Pending\
**Evidence:** Not returned

## Citation table staged migration

The target table is `s_rag_reference_citation` in the Bot-reachable GaussDB.
The Bot reads eleven public bibliography columns by `file_id`; it never
writes this table. The source is the approved Web MySQL export, and relay
mode terminates the same read in the operator's GaussDB.

### Staging and validation

1. Record source row count, export checksum, schema digest, and migration
   ticket. Do not attach source rows or credentials.
1. Create a uniquely named staging table with the source column types. Keep
   `file_id` text-compatible and retain `au`, `ti`, `so`, `vl`, `bp`, `ep`,
   `py`, `di`, `dl`, and nullable `pm`.
1. Import into the staging table only. Validate row count, duplicate
   `file_id`, null policy, UTF-8/HTML preservation, and a small owner-approved
   sample of file-id hit digests.
1. Run one direct BI smoke and one relay BI smoke against the staging data.
   Both must return only the allowlisted columns and no credential-bearing
   diagnostics.
1. With the service quiesced, perform a transactional rename: move any
   existing canonical table to a timestamped `previous` name, then rename the
   validated staging table to `s_rag_reference_citation`. This is the atomic
   rename cutover; keep the previous table through the observation window.

The expected sanitized result is equal source/staging row counts, zero
duplicate keys, approved null counts, passing direct and relay smokes, and a
successful atomic rename. A missing table or enrichment miss must remain a
title-only fallback, not a failed cited response.

### Citation rollback

If the post-cutover smoke regresses, stop new traffic, restore the previous
canonical table name in a transaction, and leave the failed table quarantined
for analysis. Do not unconditionally drop the canonical table and do not
delete the previous copy until the owner signs off. Re-run direct and relay
smokes after the rollback, then restart the service.

**Owner:** DBA / Operations / Web data owner\
**Rollback:** transactional rename to the retained previous table; restore
the prior package only if the service binary also changed.\
**Status:** External Pending\
**Evidence:** Not returned

## gaussapp, nginx, and systemd retirement

Retirement is allowed only after Bot and Web releases, history checks, and
the direct-Gauss comparison have returned evidence. Before touching a
service, capture an approved configuration backup and verify the active
listener and dependency graph.

```bash
sudo systemctl stop <legacy-gaussapp-service>
sudo cp <nginx-config> <nginx-config>.bak-<ticket>
sudo nginx -t
sudo systemctl disable --now nky_client_go.service
sudo systemctl is-enabled nky_client_go.service || true
sudo ss -ltnp | grep -E '<approved-legacy-port>|:8082' || true
```

Expected sanitized result: the legacy service is stopped, the nginx syntax
check passes before reload, the obsolete Go unit is disabled, and no
approved legacy listener remains. Keep the nginx backup and unit file until
the observation window closes. Do not remove the Python client service or
the Bot HTTP listener as part of this item.

**Owner:** Operations\
**Rollback:** restore the backed-up nginx file, re-enable/start the prior unit,
and reload nginx only after `nginx -t` passes.\
**Status:** External Pending\
**Evidence:** Not returned

## Authorized full E2E

Run the live suite only after all credentials, endpoint variables, role
grants, and owner approvals are present. The run costs real backend traffic
and is not part of the default CI gate.

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run pytest e2e/ -v --tb=short
```

Expected sanitized result: the configured suite completes with no failed
tests, and the uploaded artifact contains only the approved summary. Record
test count, pass/fail/skip counts, release commit, environment class, start/
end UTC timestamps, and the artifact id. Do not attach `e2e/output/` files
that contain customer data, keys, DSNs, SQL, or provider responses.

If the suite fails, retain the failure classification and stop the rollout;
do not rerun with broader permissions or changed secrets merely to obtain a
green result.

**Owner:** Operations with Bot/Web owners\
**Rollback:** keep the feature flag dark and restore the prior package only
through the ordered version/database procedure above.\
**Status:** External Pending\
**Evidence:** Not returned

## Acceptance ledger template

Attach one row per external action to the disposition matrix. The minimum
evidence is a sanitized command/result transcript, owner, UTC timestamp,
release or migration identifier, and rollback reference.

| Item                     | Owner          | Expected result                                         | Status           | Evidence               | Rollback reference       |
| ------------------------ | -------------- | ------------------------------------------------------- | ---------------- | ---------------------- | ------------------------ |
| GitHub variables/secrets | GitHub admin   | 16 variables + 15 secrets present; values hidden        | External Pending | Evidence: Not returned | Ticket/config backup     |
| Web API key              | Operations/Web | agents-only, 90-day key; old prefix revoked after smoke | External Pending | Evidence: Not returned | Prior protected key      |
| Gauss role/probe         | DBA/Ops        | read-only grants, `25006`, `42501`, pool reset pass     | External Pending | Evidence: Not returned | Revoke role / prior DSN  |
| Task DB rollback         | Operations     | verified backup, explicit quiescence, integrity `ok`    | External Pending | Evidence: Not returned | Immutable SQLite backup  |
| 18-query comparison      | DBA/Ops        | 18/18 fingerprint matches                               | External Pending | Evidence: Not returned | Prior service/binary     |
| Citation cutover         | DBA/Web        | staged validation, direct+relay smoke, atomic rename    | External Pending | Evidence: Not returned | Previous canonical table |
| Legacy retirement        | Operations     | service stopped, nginx/unit backup, no listener         | External Pending | Evidence: Not returned | nginx/unit backups       |
| Authorized E2E           | Ops/Bot/Web    | approved suite passes with sanitized artifact           | External Pending | Evidence: Not returned | Flag-off/prior package   |

No row may be marked closed from a local unit test, a plan checkbox, or a
health check alone.
