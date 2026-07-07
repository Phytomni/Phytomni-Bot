# Upgrade Notes

Operator upgrade manuals per release. See [CHANGELOG](../../CHANGELOG.md)
for the full change list.

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

To roll back: reinstall the 0.1.1 wheel and restore `BI_URL` /
`BI_TOKEN` in the environment. That returns the host to full 0.1.1
behavior. No data migration occurred in this release (the BI query path
change is stateless), so rollback is a plain reinstall-and-restart with
no backfill or schema step.

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
