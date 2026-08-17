# Upgrade Notes

Operator upgrade manuals per release. See [CHANGELOG](../../CHANGELOG.md)
for the full change list.

## 0.1.3 → current (`release/0.1.4`)

The installed package version is still `0.1.3`. This section covers the
operator-visible work on `release/0.1.4` since that tag. There is **no
required new secret**.

- Outbound pool variables were already required. `config/.env.example`
  now seeds LLM 32, retrieval/rerank/NL2SQL 16, and SPA FAQ 4. Restart
  each API or MCP process after changing a capacity.
- A2UI has no flag. A leftover `PHYTOMNI_A2UI_ENABLED=0` does nothing.
- Compute CPU/memory sizes and per-agent defaults are in
  [Configuration — Compute resource tiers](../reference/configuration.md#compute-resource-tiers).
  Research submissions now default to `medium` (4C/16G).

Keep the 0.1.2 → 0.1.3 sequence below for a jump from 0.1.2, including
`/openapi.json`, `/v1/agents`, `checkpoints.db`, and capability
discovery.

## 0.1.2 → 0.1.3

### Nature of This Release

0.1.3 adds persistent Review interrupt/resume, A2UI Chat/Review widgets
that shipped flag-gated, shared graph progress on HTTP SSE and MCP stdio,
richer CLI output, and the remaining dependency-audit hardening. There is
**no required new secret or environment variable** for this upgrade. The
current tree has no `A2UI_ENABLED` / `PHYTOMNI_A2UI_ENABLED` field; Chat
and Review A2UI surfaces are always on. A leftover `PHYTOMNI_A2UI_ENABLED=0`
in an old environment file has no effect.

The release also adds bounded, default-safe API limits for explicit memory,
outbound interop, and A2A projections. They are optional tuning knobs, not
schema migrations: an unchanged environment keeps the previous behavior, and
an out-of-range override fails configuration validation before the service
starts. See [Configuration](../reference/configuration.md) for the defaults
and hard ranges.

The service creates a local `checkpoints.db` beside the configured task/run
database when a persistent graph checkpoint is needed. The directory must be
writable by the service user and must stay on local storage; SQLite WAL is not
supported on a shared network filesystem. The database is additive and needs
no operator-authored migration.

### Preflight

Before stopping the 0.1.2 service, confirm the installed version and prepare a
rollback copy. Stop the service before running the SQLite backups so the
database files are not changing during the copy:

```bash
pip show mcp_server_phytomni | grep -E "^Version"
# Expect: Version: 0.1.2

systemctl stop phytomni-api
backup_dir="/backup/phytomni-0.1.3-$(date +%F)"
mkdir -p "$backup_dir"
sqlite3 "$API_KEYS_DB_PATH" ".backup $backup_dir/api_keys.sqlite"
sqlite3 "$API_TASKS_DB_PATH" ".backup $backup_dir/server_tasks.db"
if [ "${MEMORY_ENABLED:-0}" = "1" ]; then
  sqlite3 "$MEMORY_DB_PATH" ".backup $backup_dir/memory.sqlite"
fi
if [ -f checkpoints.db ]; then
  sqlite3 checkpoints.db ".backup $backup_dir/checkpoints.db"
fi
```

Keep the 0.1.2 wheel or image and its configuration available until the
post-install smoke passes. Install from the release wheel or checkout; do not
copy or create `uv.lock` in the deployment directory because this project
resolves from the declared dependency ranges.

### Deploy Sequence

1. With the preflight backup complete, install the 0.1.3 wheel or editable
   checkout and start the service again.

1. Verify the package and HTTP metadata agree:

   ```bash
   pip show mcp_server_phytomni | grep -E "^Version"
   curl -fsS http://127.0.0.1:8080/openapi.json \
     | python -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'
   ```

   Both commands must print `0.1.3`.

1. Verify the additive native-agent capability contract with an `agents`-scoped
   key:

   ```bash
   check='import json,sys; rows=json.load(sys.stdin)["data"]; '
   check+='assert len(rows)==10 and all("capabilities" in row for row in rows); '
   check+='print("10 agents with capabilities")'
   curl -fsS -H "Authorization: Bearer $KEY" \
     http://127.0.0.1:8080/v1/agents \
     | python -c "$check"
   ```

The check must print `10 agents with capabilities`. It validates the
additive discovery shape only; do not use it as a substitute for an
external agent execution test.

1. Run readiness and one authenticated model-list smoke check as described in
   [Health Checks](http-api-runbook.md#health-checks).

### Capability and Rollback Boundary

0.1.3 exposes an opt-in A2A Agent Card and authenticated `/a2a` endpoint only
when `PHYTOMNI_A2A_ENABLED=1` and a public base URL are configured. The flag
remains off by default. This release does not call external MCP/A2A peers or
provide a cross-session LangGraph Store. It does provide an independent,
opt-in explicit user-memory surface: set `MEMORY_ENABLED=1` to mount
authenticated CRUD/export/audit routes backed by local SQLite and to enable
bounded read-only Chat/Knowledge recall. Memory writes remain explicit; there
is no autonomous `langmem` writer, embedding store, or semantic index. Keep
both outbound interop and explicit memory disabled unless the deployment has
reviewed their separate operator contracts.

The Bot-local test suite and full gate prove the source contract only. Web/Go
consumer integration, DBA and operations evidence, live backend acceptance,
and production rollout remain separate deployment-owner checks. Keep every
feature flag off until the authorized change record contains the required
redacted evidence.

To roll back, reinstall 0.1.2 and restart. The 0.1.2 process ignores
`checkpoints.db`, so it may remain on disk for a later forward upgrade. Runs
paused through the 0.1.3 Review/A2UI workflow cannot be resumed by 0.1.2;
complete or abandon them before rollback. No existing task/run schema is
destructively migrated by this release.

### Operator compatibility matrix

The following matrix is the rollout contract for the opt-in surfaces. “Restart”
means restart each API worker after the environment change; the relay flag is
the one exception because it is evaluated per request.

- **Surface:** A2UI Chat/Review
  **0.1.3 default:** Off (flag-gated at ship)
  **Current tree:** Always on; no `A2UI_ENABLED` field
  **Persistent state:** `server_tasks.db`, `checkpoints.db`
  **Flag-off rollback:** Not available on the current tree. Drain or abandon
  paused A2UI runs before a rollback to 0.1.3.
  **Multi-worker limitation:** Checkpoint file is local; pin resume traffic to
  one worker.

- **Surface:** A2A server
  **0.1.3 default:** Off
  **Persistent state:** Run/task registry and checkpoints
  **Flag-off rollback:** Disable and restart; card and `/a2a` disappear without
  deleting rows.
  **Multi-worker limitation:** A2A correlations and checkpoints are
  process/local-store scoped.

- **Surface:** Outbound interop
  **0.1.3 default:** Off
  **Persistent state:** None (discovery cache is in-process)
  **Flag-off rollback:** Disable and restart; no external call or discovery
  route remains.
  **Multi-worker limitation:** Each worker has its own cache and target registry
  instance.

- **Surface:** Explicit memory
  **0.1.3 default:** Off
  **Persistent state:** `MEMORY_DB_PATH` SQLite plus mutation audit
  **Flag-off rollback:** Disable and restart; routes vanish and the file remains
  untouched.
  **Multi-worker limitation:** One local SQLite instance is not a shared
  multi-worker store.

- **Surface:** Credential relay
  **0.1.3 default:** Off
  **Persistent state:** Relay audit SQLite
  **Flag-off rollback:** Disable immediately; routes return `404` on the next
  request.
  **Multi-worker limitation:** Rate/concurrency/audit-retention state is per
  worker.

The C6.4 limit knobs are projection/admission controls only. Changing them does
not rewrite existing memory rows, run answers, A2A artifacts, or checkpoints;
it changes future writes and response projections after restart. There is no
operator-authored database migration in 0.1.3. Back up local SQLite files
before a rollout and retain the previous wheel for a reinstall-and-restart
rollback.

### Staged rollout and flag-off rollback

1. Back up `API_KEYS_DB_PATH`, `API_TASKS_DB_PATH`, `MEMORY_DB_PATH` (when
   enabled), and `checkpoints.db` while the service is stopped; use the
   [HTTP API backup procedure](http-api-runbook.md#backup-and-restore).
1. Deploy the 0.1.3 wheel with all opt-in flags unchanged (off), then run
   `/healthz`, `/readyz`, and an authenticated `/v1/models` check.
1. Enable one surface at a time on a canary worker. For A2A, configure and
   verify `A2A_PUBLIC_BASE_URL`; for interop, validate the target registry and
   encrypted credentials; for memory, use a persistent local SQLite path.
1. Run the surface-specific smoke checks in the [operator
   runbook](http-api-runbook.md),
   observe logs and resource usage, and only then roll the same environment to
   the remaining workers.
1. To roll a surface back, set its flag off and restart (or set
   `RELAY_ENABLED=0` for the immediate relay kill-switch). Confirm the route or
   request control is absent, keep the state files, and leave the rest of the
   service on 0.1.3. If the whole release must be reverted, reinstall 0.1.2,
   restart, and do not delete 0.1.3 state files.

## 0.1.1 → 0.1.2

### Nature of This Release

0.1.2 is a feature-and-hardening release on top of 0.1.1. It cuts the BI
query path (gene symbol/annotation lookups) over from the retired HTTP BI
backend to a direct GaussDB connection, adds the autonomous Expert routing
endpoint, enriches cited references with full bibliographic fields, drives
the Knowledge and Review agents through SSE streaming, and lands four
optional backend reliability knobs.

There is exactly **one required operator action**: provisioning the new
`GAUSS_DSN` secret before starting the 0.1.2 service. Everything else in
this release is additive and needs no operator change. See the
[CHANGELOG](../../CHANGELOG.md) `[0.1.2]` entry for the full change list.

### Prerequisite Check: Are You on 0.1.1?

Confirm the running stack is actually on 0.1.1 before following this
manual:

```bash
# Confirm the installed wheel version (verify on-server)
pip show mcp_server_phytomni | grep -E "^Version"

# Confirm GAUSS_DSN is absent and BI_URL / BI_TOKEN are still present.
# The example path matches the systemd EnvironmentFile from
# docs/ops/http-api-runbook.md; substitute your actual config source
# (plaintext .env, encrypted envelope, or EnvironmentFile) and verify
# on-server.
grep -E "^(BI_URL|BI_TOKEN|GAUSS_DSN)=" /etc/phytomni/api.env
```

Expect `Version: 0.1.1`, both `BI_URL` and `BI_TOKEN` present, and no
`GAUSS_DSN` line. If `GAUSS_DSN` is already set, the host has already been
migrated.

### What Changed

- **Change:** `GAUSS_DSN` now required outside relay mode
  **Operator action:** Provision the secret before starting 0.1.2 (relay
  children are exempt — see "Required Action" below).

- **Change:** `BI_URL` / `BI_TOKEN` removed
  **Operator action:** Delete both keys from your env. Harmless if left in
  place; they no longer do anything.

- **Change:** `asyncpg` new dependency
  **Operator action:** None — pulled automatically by `uv pip install -e .` /
  `uv pip install -e ".[dev,demo]"`.

- **Change:** Four optional reliability knobs added
  **Operator action:** None — all are defaulted-safe. See "Optional Reliability
  Knobs" below.

### Required Action: Provision `GAUSS_DSN`

Outside relay mode, `GAUSS_DSN` is now a required `SecretStr` field on
`SensitiveConfig` (`config/settings.py`). A service that boots without it
fails configuration validation at startup instead of falling back to the
retired HTTP BI backend.

**Exception:** a relay *child* boots without `GAUSS_DSN`. It relays BI
queries through the operator's server-side-terminated
`/v1/relay/bi/query` route instead of connecting to GaussDB directly, so
relay-child operators must **not** set `GAUSS_DSN`.

DSN format:

```text
postgresql://<GAUSS_USER>:<GAUSS_PASSWORD>@<GAUSS_HOST>:<GAUSS_PORT>/<GAUSS_DB>?sslmode=require
```

URL-encode any special character in the password (`@`, `:`, `/`, `%`, and
similar) before substituting it in — an unescaped character breaks DSN
parsing and the connection pool fails to start.

For customer images, seal `GAUSS_DSN` into the encrypted `.env.encrypted`
envelope alongside the other operator secrets; never bake the plaintext
value into an image layer.

See [Configuration](../reference/configuration.md) for the full variable
contract, including the authoritative `GAUSS_DSN` row.

### Deploy Sequence (0.1.1 → 0.1.2)

1. Stop the service (`systemctl stop phytomni-api`, or your process
   supervisor's equivalent).

1. `uv pip install -e .` (or `uv pip install -e /path/to/new/Phytomni-Bot`)
   — this also pulls `asyncpg` automatically.

1. Set `GAUSS_DSN` in your configuration source (plaintext `.env`,
   encrypted envelope, or `EnvironmentFile`), unless this host is a relay
   child.

1. Remove `BI_URL` and `BI_TOKEN` from the same configuration source.

1. Start the service (`systemctl start phytomni-api`).

1. Smoke-check readiness, then an authenticated call:

   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   curl -fsS -H "Authorization: Bearer $KEY" http://127.0.0.1:8080/v1/models
   ```

   `/readyz` must return `200`, and `/v1/models` must return the expected
   model id list. See [Health Checks](http-api-runbook.md#health-checks)
   for the full expected output.

### Rollback

`GAUSS_DSN` is additive from the 0.1.1 code's point of view — the 0.1.1
wheel never reads it and `SensitiveConfig` ignores unknown keys in the
shared `.env`, so there is no need to unset it when rolling back.

To roll back, deploy the prior wheel or image before restoring the legacy
environment after the prior binary is installed. Use this order:

1. Stop the Bot service and block new requests.
1. Deploy the prior wheel or image.
1. restore the legacy environment after the prior binary is installed; do not
   set variables that the current binary would interpret differently.
1. Start the prior service and verify its health and one read-only BI query.
1. Keep the pre-change environment and database backup until verification ends.

For the 0.1.1 prior binary, the legacy environment includes `BI_URL` and
`BI_TOKEN`. No data migration occurred in this release (the BI query path
change is stateless), so no backfill or schema step is required.

### Optional Reliability Knobs

Four new knobs are available; all are defaulted-safe, so an unchanged env
boots and behaves identically to before this release.

- **Knob:** `GAUSS_COMMAND_TIMEOUT`
  **Default:** `30.0`
  **Purpose:** Per-query timeout (seconds) for the direct GaussDB connection
  pool.

- **Knob:** `HTTP_MAX_CONNECTIONS`
  **Default:** `100`
  **Purpose:** Max total connections in the shared `httpx.AsyncClient` pool.

- **Knob:** `HTTP_MAX_KEEPALIVE`
  **Default:** `50`
  **Purpose:** Max keepalive connections in the shared `httpx.AsyncClient` pool.

- **Knob:** `API_GRACEFUL_SHUTDOWN`
  **Default:** `30`
  **Purpose:** Uvicorn graceful-shutdown drain window (seconds).

Tune `API_GRACEFUL_SHUTDOWN` only if you also tune systemd's
`TimeoutStopSec`; keep the drain window shorter than `TimeoutStopSec` so
uvicorn finishes its own shutdown before systemd sends `SIGKILL`. See
[Configuration](../reference/configuration.md) for the full description
of each knob.
