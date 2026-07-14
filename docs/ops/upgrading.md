# Upgrade Notes

Operator upgrade manuals per release. See [CHANGELOG](../../CHANGELOG.md)
for the full change list.

## 0.1.2 → 0.1.3

### Nature of This Release

0.1.3 adds persistent Review interrupt/resume, flag-gated A2UI Chat/Review
widgets, shared graph progress on HTTP SSE and MCP stdio, richer CLI output,
and the remaining dependency-audit hardening. There is **no required new
secret or environment variable** for this upgrade. `A2UI_ENABLED` /
`PHYTOMNI_A2UI_ENABLED` remains false unless an operator explicitly enables
it.

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

### Deploy Sequence

1. Confirm the installed starting version:

   ```bash
   pip show mcp_server_phytomni | grep -E "^Version"
   ```

   Expect `Version: 0.1.2`.

1. Stop the API/MCP service, install the 0.1.3 wheel or editable checkout,
   and start the service again. Do not copy or create a `uv.lock`; this
   repository resolves from the declared `pyproject.toml` ranges.

1. Verify the package and HTTP metadata agree:

   ```bash
   pip show mcp_server_phytomni | grep -E "^Version"
   curl -fsS http://127.0.0.1:8080/openapi.json \
     | python -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'
   ```

   Both commands must print `0.1.3`.

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

To roll back, reinstall 0.1.2 and restart. The 0.1.2 process ignores
`checkpoints.db`, so it may remain on disk for a later forward upgrade. Runs
paused through the 0.1.3 Review/A2UI workflow cannot be resumed by 0.1.2;
complete or abandon them before rollback. No existing task/run schema is
destructively migrated by this release.

### Operator compatibility matrix

The following matrix is the rollout contract for the opt-in surfaces. “Restart”
means restart each API worker after the environment change; the relay flag is
the one exception because it is evaluated per request.

| Surface          | 0.1.3 default | Persistent state                            | Flag-off rollback                                                     | Multi-worker limitation                                          |
| ---------------- | ------------- | ------------------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------- |
| A2UI Chat/Review | Off           | `server_tasks.db`, `checkpoints.db`         | Disable and restart; drain or abandon paused A2UI runs first.         | Checkpoint file is local; pin resume traffic to one worker.      |
| A2A server       | Off           | Run/task registry and checkpoints           | Disable and restart; card and `/a2a` disappear without deleting rows. | A2A correlations and checkpoints are process/local-store scoped. |
| Outbound interop | Off           | None (discovery cache is in-process)        | Disable and restart; no external call or discovery route remains.     | Each worker has its own cache and target registry instance.      |
| Explicit memory  | Off           | `MEMORY_DB_PATH` SQLite plus mutation audit | Disable and restart; routes vanish and the file remains untouched.    | One local SQLite instance is not a shared multi-worker store.    |
| Credential relay | Off           | Relay audit SQLite                          | Disable immediately; routes return `404` on the next request.         | Rate/concurrency/audit-retention state is per worker.            |

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
1. Run the surface-specific smoke checks in the [operator runbook](http-api-runbook.md),
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

| Change                                      | Operator action                                                                                       |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `GAUSS_DSN` now required outside relay mode | Provision the secret before starting 0.1.2 (relay children are exempt — see "Required Action" below). |
| `BI_URL` / `BI_TOKEN` removed               | Delete both keys from your env. Harmless if left in place; they no longer do anything.                |
| `asyncpg` new dependency                    | None — pulled automatically by `uv pip install -e .` / `uv pip install -e ".[dev,demo]"`.             |
| Four optional reliability knobs added       | None — all are defaulted-safe. See "Optional Reliability Knobs" below.                                |

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

### Deploy Sequence

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

| Knob                    | Default | Purpose                                                             |
| ----------------------- | ------- | ------------------------------------------------------------------- |
| `GAUSS_COMMAND_TIMEOUT` | `30.0`  | Per-query timeout (seconds) for the direct GaussDB connection pool. |
| `HTTP_MAX_CONNECTIONS`  | `100`   | Max total connections in the shared `httpx.AsyncClient` pool.       |
| `HTTP_MAX_KEEPALIVE`    | `50`    | Max keepalive connections in the shared `httpx.AsyncClient` pool.   |
| `API_GRACEFUL_SHUTDOWN` | `30`    | Uvicorn graceful-shutdown drain window (seconds).                   |

Tune `API_GRACEFUL_SHUTDOWN` only if you also tune systemd's
`TimeoutStopSec`; keep the drain window shorter than `TimeoutStopSec` so
uvicorn finishes its own shutdown before systemd sends `SIGKILL`. See
[Configuration](../reference/configuration.md) for the full description
of each knob.
