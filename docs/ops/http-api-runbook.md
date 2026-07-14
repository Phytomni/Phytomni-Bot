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
- Enabling, testing, and disabling the opt-in A2A server, outbound MCP/A2A
  discovery, explicit memory, and credential relay boundaries.

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
| Checkpoint store | `checkpoints.db`                    | ReviewAgent pause points for `/resume`.   |
| Memory store     | `.cache/phytomni/memory.sqlite`     | Opt-in user-scoped memory records.        |
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
   MEMORY_ENABLED=0
   MEMORY_DB_PATH=/var/lib/phytomni/memory.sqlite
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

| Method   | Path                                     | Auth  | Operational use                                                                                                                                    |
| -------- | ---------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/healthz`                               | no    | Process liveness.                                                                                                                                  |
| `GET`    | `/readyz`                                | no    | Store-directory writability check.                                                                                                                 |
| `GET`    | `/.well-known/agent-card.json`           | no\*  | Opt-in A2A v1 Agent Card; only present when `A2A_ENABLED=1`.                                                                                       |
| `POST`   | `/a2a`                                   | yes\* | Opt-in A2A v1 `SendMessage` / `SendStreamingMessage` / `GetTask`; requires `A2A-Version: 1.0` and the `agents` scope.                              |
| `GET`    | `/v1/interop/capabilities`               | yes   | Opt-in sanitized MCP/A2A capability discovery; only present when `INTEROP_ENABLED=1`, accepts no query overrides, and requires the `agents` scope. |
| `GET`    | `/v1/models`                             | yes   | Authenticated liveness and model map check.                                                                                                        |
| `POST`   | `/v1/chat/completions`                   | yes   | OpenAI-compatible chat-like agents.                                                                                                                |
| `GET`    | `/v1/agents`                             | yes   | Native agent slug discovery; rows carry `legacy_aliases`.                                                                                          |
| `POST`   | `/v1/agents/{agent}/runs`                | yes   | Native agent submission.                                                                                                                           |
| `POST`   | `/v1/query/route`                        | yes   | Autonomous Expert routing; one extra routing-LLM call resolves the agent per request.                                                              |
| `GET`    | `/v1/memories`                           | yes   | Lists live owner-scoped memory records; route exists only when `MEMORY_ENABLED=1`.                                                                 |
| `POST`   | `/v1/memories`                           | yes   | Creates one memory using the authenticated API-key namespace.                                                                                      |
| `GET`    | `/v1/memories/export`                    | yes   | Exports all live records in the authenticated owner's namespace; expired and foreign rows are excluded.                                            |
| `GET`    | `/v1/memories/audit`                     | svc   | Lists digest-only memory mutations for service-token operators.                                                                                    |
| `GET`    | `/v1/memories/{memory_id}`               | yes   | Owner-scoped live memory lookup.                                                                                                                   |
| `PUT`    | `/v1/memories/{memory_id}`               | yes   | Replaces a memory with an `If-Match` revision check.                                                                                               |
| `DELETE` | `/v1/memories/{memory_id}`               | yes   | Idempotent owner-scoped delete; optional `If-Match` revision check.                                                                                |
| `GET`    | `/v1/runs/{run_id}`                      | yes   | Owner-scoped run lookup.                                                                                                                           |
| `POST`   | `/v1/runs/{thread_id}/resume`            | yes   | Resume a ReviewAgent human-approval pause.                                                                                                         |
| `POST`   | `/v1/runs/{run_id}/a2ui-actions`         | yes   | Resume a ChatAgent or ReviewAgent A2UI confirm pause (`input_required`).                                                                           |
| `GET`    | `/v1/runs/{run_id}/logs`                 | yes   | Reconciled task logs for a run.                                                                                                                    |
| `GET`    | `/v1/runs`                               | yes   | Owner-scoped + service-token delegated listing.                                                                                                    |
| `POST`   | `/v1/files`                              | yes   | Per-user multipart upload (25 MiB ceiling).                                                                                                        |
| `POST`   | `/v1/api-keys`                           | svc   | Mint a per-user `ptm_...` API key (service tok).                                                                                                   |
| `GET`    | `/v1/api-keys`                           | svc   | List per-user keys (metadata only).                                                                                                                |
| `DELETE` | `/v1/api-keys/{prefix}`                  | svc   | Revoke the key with the given public prefix.                                                                                                       |
| `GET`    | `/v1/relay/audit`                        | svc   | List relay audit records (service token); filter by user, key prefix, service, status, time.                                                       |
| `GET`    | `/v1/relay/audit/{request_id}`           | svc   | Fetch relay audit records by request id (service token).                                                                                           |
| `GET`    | `/v1/relay/healthz`                      | yes   | Liveness probe for the relay; returns `{"status": "ok"}` when relay is enabled.                                                                    |
| `POST`   | `/v1/relay/llm/chat/completions`         | relay | Chat LLM relay (transparent, Bearer-injected).                                                                                                     |
| `POST`   | `/v1/relay/coder/chat/completions`       | relay | Coder model relay (transparent, Bearer-injected).                                                                                                  |
| `POST`   | `/v1/relay/embed/embeddings`             | relay | Embedding relay (transparent, Bearer-injected; OQ-001).                                                                                            |
| `POST`   | `/v1/relay/retrieve/search`              | relay | Knowledge retrieve relay (envelope, no credential).                                                                                                |
| `POST`   | `/v1/relay/rerank/rank`                  | relay | Knowledge rerank relay (envelope, no credential).                                                                                                  |
| `POST`   | `/v1/relay/database/nl2sql`              | relay | NL2SQL relay (envelope, IAM `X-Auth-Token`).                                                                                                       |
| `POST`   | `/v1/relay/bi/query`                     | relay | BI relay (envelope); server-side-terminated, no credential forwarded.                                                                              |
| `GET`    | `/v1/relay/obs/object`                   | relay | OBS object download relay (operator OBS credentials; streamed under a response-size budget, key confined to the caller tenant namespace).          |
| `GET`    | `/v1/relay/obs/list`                     | relay | OBS object list relay (operator OBS credentials; prefix confined to the caller tenant output root).                                                |
| `PUT`    | `/v1/relay/obs/object`                   | relay | OBS object upload relay (operator OBS credentials; key confined to the caller tenant namespace).                                                   |
| `PUT`    | `/v1/relay/obs/dir`                      | relay | OBS dir-marker relay (operator OBS credentials; key confined to the caller tenant namespace).                                                      |
| `POST`   | `/v1/relay/analysis/tasks`               | relay | Analysis-platform submit relay (envelope, IAM `X-Auth-Token`).                                                                                     |
| `GET`    | `/v1/relay/analysis/{task_id}`           | relay | Analysis task-status relay (envelope, IAM `X-Auth-Token`; task id validated).                                                                      |
| `GET`    | `/v1/relay/analysis/{task_id}/logs`      | relay | Analysis task-log relay (envelope, IAM; only the `task_name` query key is forwarded).                                                              |
| `POST`   | `/v1/relay/analysis/{task_id}/terminate` | relay | Analysis task-terminate relay (envelope, IAM `X-Auth-Token`; task id validated).                                                                   |
| `GET`    | `/v1/relay/spa-faq/{repo_id}`            | relay | SPA-FAQ relay (envelope, IAM `X-Auth-Token`; repo id validated; proxy-bypass; `question`/`page_size`/`page_num` only).                             |

### Memory CRUD operations (opt-in)

Set `MEMORY_ENABLED=1` and `MEMORY_DB_PATH` to a persistent local SQLite path
only when the deployment is ready to expose explicit user memory. Restart the
API after changing either value; the app mounts no memory route and opens no
memory database while the flag is false. The path must be writable by the
service account and must not be on a network filesystem.

The authenticated API key supplies the memory namespace. Do not add a
`user_id` field to create/update requests: it is rejected with `422`. Verify
the isolation smoke before rollout:

```bash
curl -fsS -H "Authorization: Bearer $ALICE_KEY" \
  -H 'content-type: application/json' \
  -d '{"kind":"note","content":"owned by alice"}' \
  http://127.0.0.1:8080/v1/memories
curl -fsS -H "Authorization: Bearer $BOB_KEY" \
  http://127.0.0.1:8080/v1/memories
```

Keep the `revision` from the create/get response and send it as
`If-Match` on updates (and optionally deletes). Missing or malformed update
headers are `428` / `400`; a stale revision is `409`. A `503 memory store unavailable` means the SQLite path is corrupt, inaccessible, or locked beyond
the configured busy timeout; fix the local volume and restart rather than
deleting the database. Back up the memory database separately from
`server_tasks.db` and `checkpoints.db`.
Use `GET /v1/memories/export` for a user-owned portability snapshot; it uses
the per-user item bound rather than the smaller graph prompt-read bound and
never exports expired rows. `expires_at` is the record TTL: list/get/export
and graph recall filter expired rows, while `MemoryStore.purge_expired()` is
the local cleanup operation. Retention deletions appear in
`memory_mutation_audit` as digest-only delete records.
The service-token-only `GET /v1/memories/audit` view exposes operation,
actor, request id, revision, and before/after SHA-256 digests only. It is
intended for incident correlation and retention checks, not content recovery.
Agents never write memory autonomously: graph recall is read-only, bounded,
and limited to the authenticated namespace. This release has no `langmem`
writer, embedding store, or semantic index. When the feature is disabled, no
memory route is mounted and no SQLite connection is opened; a graph read
failure degrades to an empty result with a sanitized warning, while API
writes fail closed with `503`.

When a graph-agent stream (`phyto-knowledge` or `phyto-brief-gene`) is
served with `stream: true`, the response carries AG-UI event frames.
Among them, an `event: Custom` frame with `name: "phyto.progress"`
delivers structured progress ticks (`phase`, `current`, `total`,
`detail`) interleaved before the terminal `TextMessageContent`.
Clients can render a progress bar from these ticks; the full AG-UI
frame inventory is documented in
[HTTP API — SSE Streaming](../reference/http-api.md#sse-streaming).
With `A2UI_ENABLED` off, ReviewAgent human-in-the-loop runs reject
`stream: true`; use non-stream review plus `/v1/runs/{id}/resume`.
With `A2UI_ENABLED` on, `phyto-review` `stream: true` emits a minimal
`phyto.a2ui` pause stream (settle `input_required`; resume via
`/resume` or `/a2ui-actions`).

### Streaming cutover checklist (ChatAgent / Instant)

Before flipping Web `bot.stream_enabled` + `VITE_STREAM_ENABLED` on:

1. Confirm Bot build includes ChatAgent streamed-answer persistence
   (settled `result.formatted.answer` is real text, not `"[streamed]"`).
1. Coordinate the Web flags so both sides enable streaming together.
1. Smoke: start a streamed Instant chat → refresh history → overlay
   answer matches what the user saw during the stream.
1. Optional: send a very long reply and confirm `truncated: true` on
   `GET /v1/runs/{id}` while the live UI still showed the full text.

### A2UI cutover checklist (ChatAgent / Instant)

Enable `PHYTOMNI_A2UI_ENABLED=1` only after the streaming cutover
above is green (P4-0): ChatAgent streamed-answer persistence must already
be live so non-A2UI traffic is safe. Additional gates:

1. Coordinate the flag with Web so both sides enable A2UI together.
1. Web must retain action transport and `run_id` while a run stays
   `input_required`, even after the SSE stream emits `RunFinished` and
   the session `finally` block would normally clear bindings; restore
   from `GET /v1/runs/{id}` when the transport drops.
1. Smoke: streamed confirm query → `phyto.a2ui` frame →
   `GET /v1/runs/{id}` shows `input_required` →
   `POST /v1/runs/{id}/a2ui-actions` with accept → run `succeeded` with
   real `formatted.answer` and `result.a2ui` `status: submitted`.

### A2UI cutover checklist (ReviewAgent)

After the ChatAgent A2UI gates above are green, extend the same
`PHYTOMNI_A2UI_ENABLED=1` flag to Review:

1. Web should send **one** uplink per pause (`/resume` **or**
   `/a2ui-actions`, not both).
1. Smoke: non-stream `phyto-review` → `input_required` with
   `interrupt.draft.a2ui` → accept via `/a2ui-actions` → `succeeded`
   with `result.a2ui` `status: submitted`.
1. Optional stream smoke: `phyto-review` `stream: true` → `phyto.a2ui`
   frame → `GET /v1/runs/{id}` `input_required` → resume as above.
1. Rollback: disable `PHYTOMNI_A2UI_ENABLED`; `/resume` remains for
   Review pauses and `phyto-review` `stream: true` returns `400`.

### A2A server cutover checklist

The A2A server is an opt-in protocol facade. It does not change the MCP tool
surface or make outbound calls to other agents. Enable it on one canary first:

1. Set `A2A_ENABLED=1` (or `PHYTOMNI_A2A_ENABLED=1`) and an absolute public
   `A2A_PUBLIC_BASE_URL` (for example `https://bot.example.com`). The base URL
   must not contain credentials, a query, or a fragment.

1. Restart the API worker. A missing or malformed public URL is a startup
   configuration error; do not work around it by exposing the loopback URL.

1. Fetch the public card and verify that it advertises exactly one JSON-RPC v1
   interface ending in `/a2a`:

   ```bash
   curl -fsS "$HOST/.well-known/agent-card.json" \
     | jq -e '(.supportedInterfaces | length) == 1 and (.supportedInterfaces[0].url | endswith("/a2a"))'
   ```

1. Use a key with the `agents` scope and send a small `SendMessage` smoke with
   `A2A-Version: 1.0`:

   ```bash
   curl -fsS -X POST "$HOST/a2a" \
     -H "Authorization: Bearer $KEY" \
     -H "A2A-Version: 1.0" \
     -H 'Content-Type: application/a2a+json' \
     -d '{"jsonrpc":"2.0","id":"smoke-1","method":"SendMessage","params":{"message":{"messageId":"msg-1","contextId":"smoke","role":"ROLE_USER","parts":[{"text":"Reply with one sentence."}]}}}'
   ```

   A successful business response is HTTP 200 with a JSON-RPC result. A 400
   means the version header or request shape is wrong; a 403 means the key is
   missing the `agents` scope.

1. For streaming, repeat the smoke with `SendStreamingMessage` and verify the
   SSE stream ends in one terminal status. For `INPUT_REQUIRED`, use the same
   task/context plus the returned generation token; a repeated or stale resume
   is rejected and must not submit the local Analyst work twice.

To roll A2A back without removing state, set `A2A_ENABLED=0`, restart, and
verify both `/.well-known/agent-card.json` and `/a2a` return `404`. Keep
`server_tasks.db` and `checkpoints.db`; a later forward deployment can still
inspect those rows. Drain or explicitly abandon `INPUT_REQUIRED` tasks before
the rollback because a disabled A2A client cannot send their resume payload.

### Bounded resource-limit checklist

The following C6.4 settings are admission or projection limits. They are read
from `ApiConfig` and should be changed one worker at a time, followed by a
restart and a focused smoke. The hard ranges prevent an accidental unbounded
override.

| Surface                | Settings and safe defaults                                                                                                                            | Operator symptom when exceeded                                                            |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Memory writes/recall   | `MEMORY_MAX_ITEMS=100`, `MEMORY_MAX_CONTENT_BYTES=16384`, `MEMORY_MAX_TOTAL_BYTES=1048576`, `MEMORY_MAX_RETRIEVAL=20`, `MEMORY_GRAPH_MAX_BYTES=65536` | API writes reject the policy violation; graph recall is bounded/truncated.                |
| Interop registry/cache | `INTEROP_MAX_TARGETS=64`, `INTEROP_CACHE_MAX_ENTRIES=256`                                                                                             | An over-sized registry fails closed; successful discovery entries evict oldest-first.     |
| A2A projections        | `A2A_MAX_HISTORY_MESSAGES=32`, `A2A_MAX_ARTIFACT_BYTES=262144`                                                                                        | `GetTask` history and local answer artifacts are capped; the underlying run is unchanged. |

Do not raise these values to hide a slow or memory-heavy backend. Check worker
RSS, SQLite disk growth, and request latency first. The interop cache and A2A
history are process-local; a restart clears cache contents but not persistent
run, checkpoint, memory, or audit files.

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
`{"approved": bool, "edits": string | null}`. When `A2UI_ENABLED` is on,
the interrupt draft may also carry `a2ui` beside the text summary.
Expect `404` for unknown or foreign runs, `409` for terminal / non-paused
runs, FastAPI `422` for malformed bodies, and `409 no pause point for run` if the registry row
exists but the graph checkpoint does not. If a resumed review pauses
again, the response stays `status: "input_required"` and returns the
next `interrupt.thread_id` / `interrupt.draft`. Success may include
`result.a2ui` when the pause carried a projected surface.

`POST /v1/runs/{run_id}/a2ui-actions` resumes ChatAgent or ReviewAgent
runs paused on an A2UI confirm surface. Dispatch keys off `run.agent`.
Requires `A2UI_ENABLED` / `PHYTOMNI_A2UI_ENABLED`
(`403 a2ui disabled` when off). Body mirrors the Web envelope
(`surface_id`, `widget`, `action_id`, `run_id`, `payload`). Expect
`404` for unknown runs, `400` for path/body `run_id` mismatch or invalid
payload, `409` for non-paused runs / surface mismatch / missing
checkpoint / duplicate POST after success. Success settles `succeeded`
with `result.formatted.answer` and `result.a2ui` marked
`props.status: submitted`.

The stdio MCP path collects the same approval payload through client
elicitation. Clients that advertise elicitation support see the draft
before resuming; clients without that capability gracefully degrade to
auto-approval so older one-shot MCP clients keep completing.

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

## Outbound Interop Operations

The outbound MCP/A2A boundary is disabled by default. It is mounted when
`INTEROP_ENABLED=1` (or `PHYTOMNI_INTEROP_ENABLED=1`) is present while the API
application starts; changing the flag, target registry, or credential envelope
requires a restart. This is intentionally different from the relay flag,
which is re-read on every request. While disabled,
`GET /v1/interop/capabilities` is absent and returns `404`.

Configure targets and credentials separately. A target registry entry may name
an HTTPS MCP URL, an absolute operator-owned stdio binary, or an A2A card base
and its allowlists. It must not contain headers, tokens, passwords, or
credential-shaped stdio args. `INTEROP_CREDENTIALS` is sensitive JSON mapping
`credential_ref` values to headers and belongs in the encrypted customer
envelope, not in the registry:

```dotenv
INTEROP_ENABLED=1
INTEROP_TARGETS='[{"id":"mcp-peer","kind":"mcp","transport":"streamable_http","url":"https://mcp.example.test/mcp","allowed_tools":["search"]}]'
INTEROP_CREDENTIALS='{"peer-token":{"headers":{"Authorization":"Bearer <operator-secret>"}}}'
```

Review stdio targets as code-execution grants to the service account. Use an
absolute fixed command, fixed args, and only the minimal environment allowlist;
do not let a request choose a binary or inherit the operator's full secret
environment. Keep the API behind a TLS-terminating edge and run it as a
dedicated unprivileged account.

Smoke the route with an `agents`-scoped key after restart:

```bash
curl -fsS \
  -H "Authorization: Bearer $KEY" \
  "$HOST/v1/interop/capabilities"
```

The response is metadata-only: `data` contains bounded capability DTOs and
`errors` contains only `target_id`, `kind`, and a stable `code`. A failed target
does not fail the other targets. The endpoint accepts no query parameters and
never executes a tool or agent. `503 interop registry unavailable` means the
operator registry or credential envelope failed local validation; fix the
configuration and restart. A successful target is cached in process memory
with its configured monotonic TTL and concurrent requests share one discovery;
error results are not long-term negative-cached, so a recovered peer can be
retried on the next request.

Research/Design execution is a separate request-level decision. Pass
`interop_mode=off` (the default) for local-only behavior, `auto` to permit a
best-effort MCP/A2A evidence lookup with a `degraded_interop` signal on local
fallback, or `required` to fail closed when no external evidence is returned.
Pass only ids in `interop_targets`. A2A `input-required` pauses before local
Analyst submission and resumes through the normal run resume endpoint. Review
`formatted.metadata.interop` for bounded target/kind/capability/status/latency
records; no peer URL, credential, or protocol correlation is exposed there.

The interop client is separate from the trusted backend pool. It ignores
environment proxies, follows no redirects, performs no transparent retry,
revalidates DNS/IP policy for each request, and injects credentials only after
the configured origin/path/TLS checks pass. HTTPS is required unless a target
explicitly permits HTTP. Private or special-use addresses require an explicit
CIDR allowlist. A2A cards are structurally validated and origin/skill
allowlisted; without a JWS trust key, only structural validation is claimed.
Only safe structured events are logged, and there is no persistent interop
audit DB. If an external peer is unavailable, use the stable target error code
and retry after correcting the peer or TTL; do not add a caller URL or command
override.

### Interop rollback and capacity

To disable outbound discovery or delegation, set `INTEROP_ENABLED=0` (or
`PHYTOMNI_INTEROP_ENABLED=0`) and restart every API worker. Confirm
`GET /v1/interop/capabilities` is absent (`404`) and that Research/Design
requests with `interop_mode=auto|required` no longer attempt an external
target. Keep the registry and encrypted credential material available for a
later forward rollout; the process cache is intentionally disposable.

The registry accepts at most `INTEROP_MAX_TARGETS` entries (default `64`, hard
range `1..256`) and each worker retains at most `INTEROP_CACHE_MAX_ENTRIES`
successful discovery projections (default `256`, hard range `1..4096`). A
`503 interop registry unavailable` after restart indicates local configuration
validation, not a peer business error: inspect the target count, JSON shape,
credential references, and endpoint allowlist before retrying. Never fix the
error by adding a caller-supplied URL or command.

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
  `GET /v1/relay/audit/{request_id}`. Rows hold redacted request/response
  bodies (request capped at `RELAY_REQUEST_AUDIT_MAX_BYTES`, response capped
  at `RELAY_RESPONSE_AUDIT_MAX_BYTES`) and the public key prefix — never the
  key hash or an injected credential header. Credential-shaped JSON and
  key/value fields are replaced with `[REDACTED]`. Treat the audit DB as
  sensitive: restrict file permissions, keep it on a local disk (SQLite WAL
  deadlocks on network filesystems), and purge any pre-existing raw-body rows
  before enabling the redaction policy in an existing deployment.
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
- **E12 checkpointer caveat.** ReviewAgent human-in-the-loop pause points
  live in the local SQLite `checkpoints.db` sibling of `server_tasks.db`.
  Run the API as one replica, or keep `/resume` and `/a2ui-actions`
  traffic pinned to a node that shares the same checkpoint file. A
  different replica can see the run row in `input_required` but miss the
  LangGraph checkpoint and return `409 no pause point for run`.

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
sqlite3 "$MEMORY_DB_PATH" ".backup /backup/$(date +%F)/memory.sqlite"
[ -f checkpoints.db ] && sqlite3 checkpoints.db \
  ".backup /backup/$(date +%F)/checkpoints.db"
[ -f "$RELAY_AUDIT_DB_PATH" ] && sqlite3 "$RELAY_AUDIT_DB_PATH" \
  ".backup /backup/$(date +%F)/relay_audit.sqlite"
```

Do not copy a WAL-mode SQLite file directly while the service is running;
the copy may miss uncheckpointed transactions.

Restore:

1. Stop the service.
1. Copy backup files into the configured paths, including `MEMORY_DB_PATH` when
   memory is enabled and `checkpoints.db` when paused Review/A2UI runs must be
   recoverable.
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
runs, tasks, ReviewAgent pause checkpoints, or on-disk function-cache rows. A
restart is required after changing A2A, interop, memory, or any C6.4 limit
setting; `RELAY_ENABLED` is the exception and is re-read per request.

Upgrade one host:

1. Remove the host from the load balancer or set its readiness weight to 0.
1. `systemctl stop phytomni-api`
1. `uv pip install -e /path/to/new/Phytomni-Bot`
1. `systemctl start phytomni-api`
1. Run the health checks above.
1. Return the host to service.

For multi-host deployments, roll one host at a time.

When a rollout changes a flag or limit, keep the old value on the remaining
hosts until the canary health, route, and resource checks pass. Do not mix
different A2A public base URLs or interop registries behind one load balancer:
clients cache the Agent Card, and each worker otherwise has an independent
discovery cache.

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

### A2A card or endpoint is unavailable

`404` for both `/.well-known/agent-card.json` and `/a2a` is the expected
flag-off state. If the flag is on, check that `A2A_PUBLIC_BASE_URL` is an
absolute HTTP(S) URL, restart the process, and fetch the card directly from
the configured public host. A card URL must end in `/a2a`; a reverse-proxy
path prefix is allowed and must be present in the configured base URL. A `400`
from `/a2a` usually means a missing or incorrect `A2A-Version: 1.0`; a `403`
usually means the API key lacks the `agents` scope.

### Interop registry unavailable or limit reached

`503 interop registry unavailable` is a local validation failure. Check the
JSON registry, `INTEROP_MAX_TARGETS`, credential references, and endpoint
allowlist, then restart. A peer timeout or malformed peer response is reported
inside the bounded `errors` list and does not expose its URL, command, headers,
or payload. If latency or memory rises after enabling interop, lower the
target/cache limits or disable the flag while investigating rather than
adding a request-level override.

### Memory write or recall is unexpectedly bounded

Check the effective `MEMORY_MAX_*` values in the service environment. A write
that exceeds content, item, or namespace policy is rejected; graph recall may
return fewer records because of the item/byte budget. These limits do not
delete existing rows. If the values are correct but writes still fail, check
the local SQLite path, free disk, and `/readyz` before changing the limits.

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
