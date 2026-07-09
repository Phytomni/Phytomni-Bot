# HTTP API Operations Runbook

This runbook is for operators who deploy `phytomni-api`, issue per-user
API keys, monitor health, rotate local stores, and triage customer tickets.
Endpoint contracts live in [HTTP API](../reference/http-api.md), and all
environment variables live in [Configuration](../reference/configuration.md).

## Scope

Covered:

- Starting and supervising the `phytomni-api` FastAPI service.
- Issuing, listing, and revoking keys with `phytomni-api-key`.
- Health and readiness probes.
- Backup and restore of local SQLite stores.
- Triage for 401, 429, stuck runs, Analyst dedup hits, and startup
  failures.

Out of scope:

- The stdio MCP server, `python -m mcp_server_phytomni.server`.
- Agent business-field semantics. Use [MCP Tool Reference](../reference/mcp-tools.md)
  for tool arguments and demo payloads.
- Building encrypted customer envelopes. Use
  [Deployment and Storage](../guides/deployment.md) for that workflow.

## Service Model

`phytomni-api` and the stdio MCP server are separate processes. They share
the in-process agent packages, but operators can start, stop, and restart
the HTTP service without managing a local MCP client session.

| Process   | Entry point                            | Transport                     | Main consumers                  |
| --------- | -------------------------------------- | ----------------------------- | ------------------------------- |
| HTTP API  | `phytomni-api`                         | TCP, default `127.0.0.1:8080` | Remote clients and integrations |
| stdio MCP | `python -m mcp_server_phytomni.server` | stdin/stdout                  | Local MCP clients               |

Local runtime files:

| Resource         | Default path                        | Purpose                                   |
| ---------------- | ----------------------------------- | ----------------------------------------- |
| API key store    | `.cache/phytomni/api_keys.sqlite`   | Per-user API key hashes and metadata.     |
| Runs/tasks store | `server_tasks.db`                   | Run tracking and child task linkage.      |
| Function cache   | `.cache/phytomni/func_cache.sqlite` | Cached LLM/retrieval/database primitives. |

Defaults are relative to the process working directory. In systemd or
container deployments, pin `WorkingDirectory=` or configure absolute store
paths through the environment.

## Deployment Checklist

1. Install Python 3.12, 3.13, or 3.14.

1. Create a virtual environment and install the package:

   ```bash
   python3.12 -m venv /opt/phytomni/venv
   source /opt/phytomni/venv/bin/activate
   uv pip install -e /path/to/Phytomni-Bot
   ```

1. Provide configuration through one supported path:

   - plaintext `src/mcp_server_phytomni/config/.env` for local/internal
     deployments
   - `.env.encrypted` plus `PHYTOMNI_LICENSE_KEY` for trusted customer
     images

1. Set absolute SQLite paths for production:

   ```bash
   API_KEYS_DB_PATH=/var/lib/phytomni/api_keys.sqlite
   API_TASKS_DB_PATH=/var/lib/phytomni/server_tasks.db
   PHYTOMNI_CACHE_DB=/var/lib/phytomni/func_cache.sqlite
   ```

1. Start the service:

   ```bash
   phytomni-api
   ```

1. Verify liveness and readiness:

   ```bash
   curl -fsS http://127.0.0.1:8080/healthz
   curl -fsS http://127.0.0.1:8080/readyz
   ```

## systemd Example

`/etc/systemd/system/phytomni-api.service`:

```ini
[Unit]
Description=Phytomni HTTP API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=phytomni
Group=phytomni
WorkingDirectory=/var/lib/phytomni
EnvironmentFile=/etc/phytomni/api.env
ExecStart=/opt/phytomni/venv/bin/phytomni-api
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/phytomni

[Install]
WantedBy=multi-user.target
```

Start and follow logs:

```bash
systemctl enable --now phytomni-api
journalctl -u phytomni-api -f
```

Expose the service externally through a TLS-terminating reverse proxy or a
VPN/firewall allow-list. Do not use uvicorn as the public edge.

## API Key Administration

Create a key:

```bash
phytomni-api-key create --user-id alice --name laptop
```

Create a key that expires:

```bash
phytomni-api-key create --user-id alice --name trial --expires-days 30
```

List keys:

```bash
phytomni-api-key list
phytomni-api-key list --user-id alice
```

Revoke by prefix:

```bash
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

The plaintext key is printed once by `create` and cannot be recovered
later. Clients can send it as either:

```text
Authorization: Bearer ptm_...
X-API-Key: ptm_...
```

Use [CLI Reference](../reference/cli.md) for the complete command reference.

## Endpoint Inventory

| Method   | Path                                     | Auth  | Operational use                                                                                                                           |
| -------- | ---------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/healthz`                               | no    | Process liveness.                                                                                                                         |
| `GET`    | `/readyz`                                | no    | Store-directory writability check.                                                                                                        |
| `GET`    | `/v1/models`                             | yes   | Authenticated liveness and model map check.                                                                                               |
| `POST`   | `/v1/chat/completions`                   | yes   | OpenAI-compatible chat-like agents.                                                                                                       |
| `GET`    | `/v1/agents`                             | yes   | Native agent slug discovery; rows carry `legacy_aliases`.                                                                                 |
| `POST`   | `/v1/agents/{agent}/runs`                | yes   | Native agent submission.                                                                                                                  |
| `POST`   | `/v1/query/route`                        | yes   | Autonomous Expert routing; one extra routing-LLM call resolves the agent per request.                                                     |
| `GET`    | `/v1/runs/{run_id}`                      | yes   | Owner-scoped run lookup.                                                                                                                  |
| `POST`   | `/v1/runs/{thread_id}/resume`            | yes   | Resume a ReviewAgent human-approval pause.                                                                                                |
| `GET`    | `/v1/runs/{run_id}/logs`                 | yes   | Reconciled task logs for a run.                                                                                                           |
| `GET`    | `/v1/runs`                               | yes   | Owner-scoped + service-token delegated listing.                                                                                           |
| `POST`   | `/v1/files`                              | yes   | Per-user multipart upload (25 MiB ceiling).                                                                                               |
| `POST`   | `/v1/api-keys`                           | svc   | Mint a per-user `ptm_...` API key (service tok).                                                                                          |
| `GET`    | `/v1/api-keys`                           | svc   | List per-user keys (metadata only).                                                                                                       |
| `DELETE` | `/v1/api-keys/{prefix}`                  | svc   | Revoke the key with the given public prefix.                                                                                              |
| `GET`    | `/v1/relay/audit`                        | svc   | List relay audit records (service token); filter by user, key prefix, service, status, time.                                              |
| `GET`    | `/v1/relay/audit/{request_id}`           | svc   | Fetch relay audit records by request id (service token).                                                                                  |
| `GET`    | `/v1/relay/healthz`                      | yes   | Liveness probe for the relay; returns `{"status": "ok"}` when relay is enabled.                                                           |
| `POST`   | `/v1/relay/llm/chat/completions`         | relay | Chat LLM relay (transparent, Bearer-injected).                                                                                            |
| `POST`   | `/v1/relay/coder/chat/completions`       | relay | Coder model relay (transparent, Bearer-injected).                                                                                         |
| `POST`   | `/v1/relay/embed/embeddings`             | relay | Embedding relay (transparent, Bearer-injected; OQ-001).                                                                                   |
| `POST`   | `/v1/relay/retrieve/search`              | relay | Knowledge retrieve relay (envelope, no credential).                                                                                       |
| `POST`   | `/v1/relay/rerank/rank`                  | relay | Knowledge rerank relay (envelope, no credential).                                                                                         |
| `POST`   | `/v1/relay/database/nl2sql`              | relay | NL2SQL relay (envelope, IAM `X-Auth-Token`).                                                                                              |
| `POST`   | `/v1/relay/bi/query`                     | relay | BI relay (envelope); server-side-terminated, no credential forwarded.                                                                     |
| `GET`    | `/v1/relay/obs/object`                   | relay | OBS object download relay (operator OBS credentials; streamed under a response-size budget, key confined to the caller tenant namespace). |
| `GET`    | `/v1/relay/obs/list`                     | relay | OBS object list relay (operator OBS credentials; prefix confined to the caller tenant output root).                                       |
| `PUT`    | `/v1/relay/obs/object`                   | relay | OBS object upload relay (operator OBS credentials; key confined to the caller tenant namespace).                                          |
| `PUT`    | `/v1/relay/obs/dir`                      | relay | OBS dir-marker relay (operator OBS credentials; key confined to the caller tenant namespace).                                             |
| `POST`   | `/v1/relay/analysis/tasks`               | relay | Analysis-platform submit relay (envelope, IAM `X-Auth-Token`).                                                                            |
| `GET`    | `/v1/relay/analysis/{task_id}`           | relay | Analysis task-status relay (envelope, IAM `X-Auth-Token`; task id validated).                                                             |
| `GET`    | `/v1/relay/analysis/{task_id}/logs`      | relay | Analysis task-log relay (envelope, IAM; only the `task_name` query key is forwarded).                                                     |
| `POST`   | `/v1/relay/analysis/{task_id}/terminate` | relay | Analysis task-terminate relay (envelope, IAM `X-Auth-Token`; task id validated).                                                          |
| `GET`    | `/v1/relay/spa-faq/{repo_id}`            | relay | SPA-FAQ relay (envelope, IAM `X-Auth-Token`; repo id validated; proxy-bypass; `question`/`page_size`/`page_num` only).                    |

When a graph-agent stream (`phyto-knowledge`, `phyto-review`, or
`phyto-brief-gene`) is served with `stream: true`, the response
carries AG-UI event frames. Among them, an `event: Custom` frame
with `name: "phyto.progress"` delivers structured progress ticks
(`phase`, `current`, `total`, `detail`) interleaved before the
terminal `TextMessageContent`. Clients can render a progress bar
from these ticks; the full AG-UI frame inventory is documented in
[HTTP API — SSE Streaming](../reference/http-api.md#sse-streaming).

The OBS relay rows confine each object key to the caller's tenant namespace
(`agent_data/{user_data,uploads}/<user_id>/`), with one read-only exception:
`GET /v1/relay/obs/object` also serves the content-addressed
`agent_data/shared/<fingerprint>/` store on a possession-of-fingerprint basis
(a full 64-hex fingerprint segment is required; the bare shared root is
rejected to prevent enumeration), so a dedup-reuse caller can fetch a prior
tenant's shared output without holding that tenant's namespace.

`DataAgent` is a synchronous native run: the HTTP layer returns its result
inline with status `200`.

`GET /v1/runs` accepts these query parameters beyond the basic set:
`user_id=<other>` requires `X-Service-Token` (returns `403` without
it) and lists any tenant's runs; `dialogue_id=<id>` filters to one
chat-ai conversation thread server-side so a paginated history
loads only the target conversation; `created_after=<iso-8601>` /
`created_before=<iso-8601>` apply inclusive ISO-8601 date bounds;
`debug=true` keeps the full `result.raw` payload on each row. Each
row carries `dialogue_id` / `query` / `tool_name` / `model` /
`answer` alongside the standard fields, sourced from
`result.formatted.answer`.

`POST /v1/runs/{thread_id}/resume` is valid only for owner-scoped
ReviewAgent rows in `input_required`. The body is
`{"approved": bool, "edits": string | null}`. Expect `404` for unknown
or foreign runs, `409` for terminal / non-paused runs, and `409 no pause point for run` if the registry row exists but the graph checkpoint
does not.

`GET /v1/runs/{run_id}/logs` returns reconciled task logs for a run.
The endpoint verifies ownership, then fetches or retrieves cached logs
for each task in the run. The response is locked to exactly three
top-level keys — `run_id`, `task_ids`, and `task_logs` (a list of the
reconciled per-task log payloads); no `object` discriminator, no
top-level `init_info` / `steps` / `tasks` lifts. Default mode strips
the raw handler payload from each task log; pass `debug=true` to
include it. Returns `404` if the run is unknown or owned by another
user. The route does not accept a delegated `?user_id=` query: under
the candidate-A architecture every log already belongs to the single
`web` user, and broadening to multi-tenant delegation would land
alongside the candidate-B owner-key revival.

`POST /v1/files` accepts one `multipart/form-data` upload through the
standard `file` field with an optional `purpose` field. The
`purpose` value MUST be one of `agent_context` (default) /
`assistants` / `batch` / `fine-tune` / `vision` / `user_data`; any
other value returns `422`. Stored under
`agent_data/uploads/{user_id}/{request_id}/{file_id}/{safe_filename}`;
the response carries the OpenAI-files compatible shape plus
`obs_path` (the public `/obs/<bucket>/<key>` form) and a `path` alias
so clients can replay it in any later `obs_file_list` argument.

Default size ceiling is `API_UPLOAD_MAX_BYTES` (25 MiB). The route
defends in two layers: a `Content-Length` pre-check rejects honest
oversize requests before reading, and a chunked reader caps
cumulative reads when `Content-Length` is absent or falsified
(`Transfer-Encoding: chunked`), aborting at the first chunk that
pushes past the limit so peak memory stays bounded near the ceiling.
Both paths return `413`.

Filename sanitization is a deliberate **sanitize-and-accept** policy:
path-traversal segments collapse to the basename
(`../../etc/passwd` → `passwd`, response `201`) and unsafe stem
characters rewrite to `-` (`my report (final).pdf` →
`my-report-final.pdf`, response `201`). Only empty bodies and
empty / `.` / `..` filenames return `400`.

## Relay Operations

The credential-injecting relay (`/v1/relay/*`) is off unless
`RELAY_ENABLED=1`. Operate it as follows.

- **Enable / disable.** Set `RELAY_ENABLED=1` to expose the surface;
  set it back to `0` to disable. The flag is re-read per request, so a
  disable takes effect on in-flight workers without a restart — this is
  the incident kill-switch. While disabled, every relay route returns
  `404`.
- **Issue customer keys.** Mint a `ptm_...` key scoped to only the
  services the customer may reach:
  `phytomni-api-key create --user-id <customer> --scope relay:llm --scope relay:retrieve`.
  Use `--scope relay:*` for all relay services. Do **not** issue a
  scope-less key for relay use — scope-less keys are all-access on the
  agent routes but are denied (`403`) on relay routes by design.
- **Per-service upstream auth.** `llm` / `coder` / `embed` inject the
  operator `Authorization: Bearer` key; `database` / `analysis` inject an
  IAM `X-Auth-Token`; `bi` is server-side-terminated (the operator runs
  `gauss_query` locally — no credential is forwarded to the child);
  `retrieve` / `rerank` inject nothing (their upstreams are currently
  unauthenticated). The operator's real secrets come from the same `.env`
  / `.env.encrypted` the rest of the service uses (`API_KEY`,
  `CODER_API_KEY`, `EMBED_API_KEY`, and the IAM user credentials); no
  relay-specific secret exists.
- **Query the audit.** Every relay call is recorded in the local audit
  store (`RELAY_AUDIT_DB_PATH`). Query it with the service token:
  `GET /v1/relay/audit?service=llm&user_id=<customer>` and
  `GET /v1/relay/audit/{request_id}`. Rows hold the verbatim
  request/response bodies (response capped at
  `RELAY_RESPONSE_AUDIT_MAX_BYTES`) and the public key prefix — never the
  key hash or an injected credential header. Treat the audit DB as
  sensitive (it can contain raw customer payloads): restrict file
  permissions and keep it on a local disk (SQLite WAL deadlocks on
  network filesystems).
- **Retention.** Audit rows are eligible for cleanup after
  `RELAY_AUDIT_RETENTION_DAYS` (default 90). Purge expired rows on a
  schedule by calling `RelayAuditStore.purge_expired(retention_days)`
  (wire it into a cron job alongside the existing task cleanup).
- **Rate and concurrency.** Relay calls draw on a per-key budget
  (`RELAY_RATE_LIMIT_PER_MIN`, returns `429` + `Retry-After`) that is
  separate from the agent budget, and each key may hold at most
  `RELAY_MAX_CONCURRENT_PER_KEY` in-flight forwards (excess returns
  `503`). Both counters are per worker; raise the limits cautiously since
  relay calls spend the operator's metered upstream credentials.
- **Multi-worker caveat.** The rate, concurrency, and audit-retention
  state are per worker. With N workers the effective per-key ceilings are
  ×N, so set the limits accordingly or front the relay with a single
  worker until a shared store is added.
- **Live-task reconciliation is process-local.** The in-flight
  `deep_genome` umbrella registry (`runtime/live_tasks.py`) that lets
  `GetTaskStatus` and `GET /v1/runs/{run_id}` tell a live umbrella from a
  dead one lives in one process's memory. Under multiple workers a poll
  served by a worker that did not launch the umbrella reads it as dead
  and can reconcile a still-running run to `failed`. Run the API
  single-worker (the default) until the registry moves to shared storage.

## Health Checks

Liveness:

```bash
curl -fsS http://127.0.0.1:8080/healthz
```

Expected body:

```json
{"status":"ok"}
```

Readiness:

```bash
curl -i http://127.0.0.1:8080/readyz
```

`200` means the configured API key and task store directories are writable.
`503` means at least one store directory is not writable. Fix permissions
or repoint the store paths, then restart.

Authenticated smoke:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/models"
```

Expected model ids:

- `phyto-chat`
- `phyto-knowledge`
- `phyto-review`
- `phyto-brief-gene`

## Backup and Restore

Back up SQLite stores with SQLite's `.backup` command:

```bash
mkdir -p "/backup/$(date +%F)"
sqlite3 "$API_KEYS_DB_PATH" ".backup /backup/$(date +%F)/api_keys.sqlite"
sqlite3 "$API_TASKS_DB_PATH" ".backup /backup/$(date +%F)/server_tasks.db"
```

Do not copy a WAL-mode SQLite file directly while the service is running;
the copy may miss uncheckpointed transactions.

Restore:

1. Stop the service.
1. Copy backup files into the configured paths.
1. Start the service.
1. Run `/readyz` and `/v1/models` smoke checks.

The function cache is recoverable and can usually be rebuilt by traffic.
Back it up only when preserving expensive cached remote results matters.

## Restart and Upgrade

Restart:

```bash
systemctl restart phytomni-api
journalctl -u phytomni-api -n 50 -f
```

A restart clears in-memory rate-limit counters. It does not clear API keys,
runs, tasks, or on-disk function-cache rows.

Upgrade one host:

1. Remove the host from the load balancer or set its readiness weight to 0.
1. `systemctl stop phytomni-api`
1. `uv pip install -e /path/to/new/Phytomni-Bot`
1. `systemctl start phytomni-api`
1. Run the health checks above.
1. Return the host to service.

For multi-host deployments, roll one host at a time.

## Triage SOPs

### 401 Unauthorized

Check in order:

1. The customer copied the key incorrectly. Compare the key prefix against
   `phytomni-api-key list --user-id <user>`.
1. The key was revoked. `list` shows `ACTIVE=False`.
1. The key expired.
1. The service and CLI point at different `API_KEYS_DB_PATH` values.

Do not ask the customer to resend full keys through tickets. Prefix plus
user id is enough for store-side checks.

### 429 Rate Limited

Read the `Retry-After` response header and tell the client to back off for
that many seconds.

For sustained legitimate traffic, either raise `API_RATE_LIMIT_PER_MIN`
and restart, or issue additional keys for the same user and let the client
rotate among them.

The limiter is per process. Restarting the service clears the current
counter.

### Run Is Stuck

Ask for the `run_id` from the `202` response, then fetch:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/runs/$RUN_ID"
```

Interpretation:

- `status == "running"`: still in flight. Remote agents can take tens of
  minutes to several hours.
- `status == "failed"`: inspect the `error` field and escalate to
  application engineers.
- `status == "succeeded"` but the customer did not receive it: check
  whether the success row expired. Default successful-run TTL is 24 hours.

If the customer has only a `task_id`, query the task store directly:

```bash
sqlite3 "$API_TASKS_DB_PATH" \
  "SELECT run_id, agent, status FROM tasks WHERE task_id = '$TASK_ID';"
```

### Analyst Returned `id: null`

This shape now indicates a **local registry write failure**, not a dedup hit.
The response body carries `"degraded_tracking": true`:

```json
{
  "id": null,
  "object": "agent.run",
  "agent": "analyst",
  "status": "running",
  "task_ids": [],
  "degraded_tracking": true,
  "result": { ... }
}
```

The remote task was accepted by the analysis platform but the local SQLite
registry write failed (check server logs for the `sqlite3.Error` / `OSError`
traceback). The task is live upstream; use the analysis platform's own task
listing to locate it, or wait for a reconcile pass.

Duplicate submissions are now handled transparently: a fingerprint match mints
a fresh caller-owned task id and returns a normal `202` body with a valid `id`
and `task_ids`. Clients no longer need to handle a `dedup_hit` field.

### Chat Completion Carries `run_id: null` or `degraded_tracking: true`

A `/v1/chat/completions` response with HTTP 200 can still carry
`"run_id": null` and `"degraded_tracking": true`. This means the chat
completion itself succeeded (the customer received a valid answer) but
the local SQLite registry write (`_record_sync_run`) failed, so the
run row was not persisted.

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "run_id": null,
  "degraded_tracking": true,
  "choices": [ ... ]
}
```

Impact: `GET /v1/runs?dialogue_id=...` will **not** return this call,
and `GET /v1/runs/{run_id}` cannot be used to replay it. The customer
has the answer, but the run history has a gap.

Check server logs for `sync run bookkeeping write failed for agent <slug>: <ExceptionClass>` (logged at WARNING level by `_record_sync_run`). The
underlying cause is almost always a `sqlite3.Error` or `OSError` on the
`server_tasks.db` file — check disk fullness, WAL checkpoint health,
and file permissions. Once the store is healthy, subsequent calls
resume writing normally; the lost run row cannot be recovered (it was
never persisted).

This signal mirrors the remote-agent `degraded_tracking` flag (see
"Analyst Returned `id: null`" above) but on the sync path. The two
cases share the same client-side meaning: the remote or local
operation succeeded, but local bookkeeping did not.

### BriefGene Answer Is Generic Or Hits `nogeneid`

Clients sometimes report that `phyto-brief-gene` returns a vague answer
even though their query mentioned a real gene. The root cause is almost
always that the client submitted a research question or a gene symbol
plus species rather than a single canonical locus id, so `BriefGeneAgent`
fell into its `nogeneid` fallback prompt.

Advise the client to set `resolve_gene_id: true` on the request:

```bash
curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  "$HOST/v1/chat/completions" \
  -d '{"model":"phyto-brief-gene","resolve_gene_id":true,"messages":[{"role":"user","content":"What does AT5G42800 do in Arabidopsis?"}]}'

curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  "$HOST/v1/agents/brief_gene/runs" \
  -d '{"arguments":{"user_query":"rice TPR6 function","resolve_gene_id":true}}'
```

On `/v1/chat/completions` the flag is BriefGene-only — sending it with
any other `model` returns `400`. On the native `/v1/agents/{slug}/runs`
path `resolve_gene_id` also serves the `deep_genome` and `design` slugs
(where it injects `species_code` alongside `gene_id`); an ineligible
slug still returns `400` naming the resolver reason. Resolver failures
(blank input, empty candidates, non-JSON LLM output) also return `400`
and the response body's `error.message` carries the resolver reason for
ticket triage. On success the response `metadata` includes
`original_query`, `resolved_gene_id`, `resolved_species_code`, and
`resolve_gene_id: true` so support can confirm which canonical id and
species BriefGene actually saw.

The resolver adds one shared-cache LLM call per unique free-form query,
so heavy unsupervised opt-in does add LLM cost; the `~90d` `phyto_chat`
cache keeps the marginal cost near zero for repeated identical queries.

### Upload Returned 413, 422, Or 400

`POST /v1/files` enforces three guard rails. Triage by code:

- `413` — the upload exceeds `API_UPLOAD_MAX_BYTES` (default 25 MiB).
  Two layers fire: the route's `Content-Length` pre-check rejects
  honest oversize requests before reading, and the chunked reader
  (`read_with_byte_budget`, 64 KiB chunks) catches absent / falsified
  `Content-Length` (chunked transfer encoding) by aborting the read
  loop the moment cumulative bytes cross the ceiling. A sustained
  413 stream indicates either a misconfigured client or a deliberate
  ceiling bump request. Raise the env var and restart to widen.
- `422` — the supplied `purpose` form field is outside the allowed
  `Literal` enum (`agent_context` / `assistants` / `batch` /
  `fine-tune` / `vision` / `user_data`). FastAPI's
  `RequestValidationError` flows through the unified envelope.
  Tell the client to send one of the six allowed values; do NOT
  silently accept arbitrary purpose strings.
- `400` — the upload body is empty, the `file` form field is
  missing, or the supplied filename is empty / `.` / `..`. The
  unified error envelope carries the rejection reason in
  `error.message`. Filename sanitization itself never returns `400`;
  traversal segments collapse to the basename silently and return
  `201` with the sanitized name.

Stored uploads live under
`agent_data/uploads/{user_id}/{request_id}/{file_id}/{safe_filename}`
in OBS. There is no GC; orphaned uploads stay forever until the bucket
TTL or an out-of-band sweep removes them.

### Startup Failure

Common failures:

| Error                                | Meaning                                                                                           | Fix                                                                                                                                                             |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SecretEnvelopeError`                | Wrong license key, damaged `.env.encrypted`, or an envelope sealed from a non-UTF-8 / BOM `.env`. | Verify `PHYTOMNI_LICENSE_KEY`; if the message names a UTF-8 / BOM problem, rebuild the envelope from a UTF-8 (no-BOM) source. Otherwise redeliver the envelope. |
| `RuntimeError` resolving environment | No plaintext `.env`, no encrypted envelope, no testing mode.                                      | Provide one supported config source.                                                                                                                            |
| `PermissionError` on SQLite path     | Store directory is not writable.                                                                  | Fix permissions or configure absolute store paths.                                                                                                              |
| `OSError: [Errno 98]`                | Port already bound.                                                                               | Free the port or change `API_PORT`.                                                                                                                             |

If `/readyz` returns 200 but authenticated endpoints return 500, inspect
stderr or `journalctl -u phytomni-api` and escalate with the request id from
the response header.
