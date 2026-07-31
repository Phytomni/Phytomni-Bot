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
- Agent business-field semantics. Use [MCP Tool
  Reference](../reference/mcp-tools.md)
  for tool arguments and demo payloads.
- Building encrypted customer envelopes. Use
  [Deployment and Storage](../guides/deployment.md) for that workflow.

## Current-SHA contract acceptance

Use the [Bot contract acceptance runbook](bot-contract-acceptance-runbook.md)
for the exact focused test packet, full-gate and Python-matrix evidence,
fixture hashes, and twelve deployment smokes. A green local Bot packet means
`Bot Ready`; it does not mean `Accepted`. Web/Go forwarding, staging, live
backend, and production evidence remain `External Pending` until the owner
returns a redacted artifact for the same Bot SHA. Keep feature flags dark
while those paired checks are absent. DataAgent root-cause replay and Analyst
historical repair remain separately authorized operations.

## Service Model

`phytomni-api` and the stdio MCP server are separate processes. They share
the in-process agent packages, but operators can start, stop, and restart
the HTTP service without managing a local MCP client session.

- **Process:** HTTP API
  **Entry point:** `phytomni-api`
  **Transport:** TCP, default `127.0.0.1:8080`
  **Main consumers:** Remote clients and integrations

- **Process:** stdio MCP
  **Entry point:** `python -m mcp_server_phytomni.server`
  **Transport:** stdin/stdout
  **Main consumers:** Local MCP clients

Local runtime files:

- **Resource:** API key store
  **Default path:** `.cache/phytomni/api_keys.sqlite`
  **Purpose:** Per-user API key hashes and metadata.

- **Resource:** Runs/tasks store
  **Default path:** `server_tasks.db`
  **Purpose:** Run tracking and child task linkage.

- **Resource:** Checkpoint store
  **Default path:** `checkpoints.db`
  **Purpose:** ReviewAgent pause points for `/resume`.

- **Resource:** Memory store
  **Default path:** `.cache/phytomni/memory.sqlite`
  **Purpose:** Opt-in user-scoped memory records.

- **Resource:** Function cache
  **Default path:** `.cache/phytomni/func_cache.sqlite`
  **Purpose:** Cached LLM/retrieval/database primitives.

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

- **Method:** `GET`
  **Path:** `/healthz`
  **Auth:** no
  **Operational use:** Process liveness.

- **Method:** `GET`
  **Path:** `/readyz`
  **Auth:** no
  **Operational use:** Store-directory writability check.

- **Method:** `GET`
  **Path:** `/.well-known/agent-card.json`
  **Auth:** no\*
  **Operational use:** Opt-in A2A v1 Agent Card; only present when
  `A2A_ENABLED=1`.

- **Method:** `POST`
  **Path:** `/a2a`
  **Auth:** yes\*
  **Operational use:** Opt-in A2A v1 `SendMessage` / `SendStreamingMessage` /
  `GetTask`; requires `A2A-Version: 1.0` and the `agents`
  scope.

- **Method:** `GET`
  **Path:** `/v1/interop/capabilities`
  **Auth:** yes
  **Operational use:** Opt-in sanitized MCP/A2A capability discovery; only
  present when `INTEROP_ENABLED=1`, accepts no query
  overrides, and requires the
  `agents` scope.

- **Method:** `GET`
  **Path:** `/v1/models`
  **Auth:** yes
  **Operational use:** Authenticated liveness and model map check.

- **Method:** `POST`
  **Path:** `/v1/chat/completions`
  **Auth:** yes
  **Operational use:** OpenAI-compatible chat-like agents.

- **Method:** `GET`
  **Path:** `/v1/agents`
  **Auth:** yes
  **Operational use:** Native agent slug discovery; rows carry `legacy_aliases`
  and additive `capabilities`.

- **Method:** `POST`
  **Path:** `/v1/agents/{agent}/runs`
  **Auth:** yes
  **Operational use:** Native agent submission.

- **Method:** `POST`
  **Path:** `/v1/query/route`
  **Auth:** yes
  **Operational use:** Autonomous Expert routing; one extra routing-LLM call
  resolves the agent per request.

- **Method:** `GET`
  **Path:** `/v1/memories`
  **Auth:** yes
  **Operational use:** Lists live owner-scoped memory records; route exists only
  when `MEMORY_ENABLED=1`.

- **Method:** `POST`
  **Path:** `/v1/memories`
  **Auth:** yes
  **Operational use:** Creates one memory using the authenticated API-key
  namespace.

- **Method:** `GET`
  **Path:** `/v1/memories/export`
  **Auth:** yes
  **Operational use:** Exports all live records in the authenticated owner's
  namespace; expired and foreign rows are excluded.

- **Method:** `GET`
  **Path:** `/v1/memories/audit`
  **Auth:** svc
  **Operational use:** Lists digest-only memory mutations for service-token
  operators.

- **Method:** `GET`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Operational use:** Owner-scoped live memory lookup.

- **Method:** `PUT`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Operational use:** Replaces a memory with an `If-Match` revision check.

- **Method:** `DELETE`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Operational use:** Idempotent owner-scoped delete; optional `If-Match`
  revision check.

- **Method:** `GET`
  **Path:** `/v1/runs/{run_id}`
  **Auth:** yes
  **Operational use:** Owner-scoped run lookup.

- **Method:** `POST`
  **Path:** `/v1/runs/{thread_id}/resume`
  **Auth:** yes
  **Operational use:** Resume a ReviewAgent human-approval pause.

- **Method:** `POST`
  **Path:** `/v1/runs/{run_id}/a2ui-actions`
  **Auth:** yes
  **Operational use:** Resume a ChatAgent or ReviewAgent A2UI confirm pause
  (`input_required`).

- **Method:** `GET`
  **Path:** `/v1/runs/{run_id}/logs`
  **Auth:** yes
  **Operational use:** Reconciled task logs for a run.

- **Method:** `GET`
  **Path:** `/v1/runs`
  **Auth:** yes
  **Operational use:** Owner-scoped + service-token delegated listing.

- **Method:** `POST`
  **Path:** `/v1/files`
  **Auth:** yes
  **Operational use:** Per-user multipart upload (25 MiB ceiling).

- **Method:** `POST`
  **Path:** `/v1/api-keys`
  **Auth:** svc
  **Operational use:** Mint a per-user `ptm_...` API key (service tok).

- **Method:** `GET`
  **Path:** `/v1/api-keys`
  **Auth:** svc
  **Operational use:** List per-user keys (metadata only).

- **Method:** `DELETE`
  **Path:** `/v1/api-keys/{prefix}`
  **Auth:** svc
  **Operational use:** Revoke the key with the given public prefix.

- **Method:** `GET`
  **Path:** `/v1/relay/audit`
  **Auth:** svc
  **Operational use:** List relay audit records (service token); filter by user,
  key prefix, service, status, time.

- **Method:** `GET`
  **Path:** `/v1/relay/audit/{request_id}`
  **Auth:** svc
  **Operational use:** Fetch relay audit records by request id (service token).

- **Method:** `GET`
  **Path:** `/v1/relay/healthz`
  **Auth:** yes
  **Operational use:** Liveness probe for the relay; returns `{"status": "ok"}`
  when relay is enabled.

- **Method:** `POST`
  **Path:** `/v1/relay/llm/chat/completions`
  **Auth:** relay
  **Operational use:** Chat LLM relay (transparent, Bearer-injected).

- **Method:** `POST`
  **Path:** `/v1/relay/coder/chat/completions`
  **Auth:** relay
  **Operational use:** Coder model relay (transparent, Bearer-injected).

- **Method:** `POST`
  **Path:** `/v1/relay/embed/embeddings`
  **Auth:** relay
  **Operational use:** Embedding relay (transparent, Bearer-injected; OQ-001).

- **Method:** `POST`
  **Path:** `/v1/relay/retrieve/search`
  **Auth:** relay
  **Operational use:** Knowledge retrieve relay (envelope, no credential).

- **Method:** `POST`
  **Path:** `/v1/relay/rerank/rank`
  **Auth:** relay
  **Operational use:** Knowledge rerank relay (envelope, no credential).

- **Method:** `POST`
  **Path:** `/v1/relay/database/nl2sql`
  **Auth:** relay
  **Operational use:** NL2SQL relay (envelope, IAM `X-Auth-Token`).

- **Method:** `POST`
  **Path:** `/v1/relay/bi/query`
  **Auth:** relay
  **Operational use:** BI relay (envelope); server-side-terminated, no
  credential forwarded.

- **Method:** `GET`
  **Path:** `/v1/relay/obs/object`
  **Auth:** relay
  **Operational use:** OBS object download relay (operator OBS credentials;
  streamed under a response-size budget, key confined to
  the caller tenant
  namespace).

- **Method:** `GET`
  **Path:** `/v1/relay/obs/list`
  **Auth:** relay
  **Operational use:** OBS object list relay (operator OBS credentials; prefix
  confined to the caller tenant output root).

- **Method:** `PUT`
  **Path:** `/v1/relay/obs/object`
  **Auth:** relay
  **Operational use:** OBS object upload relay (operator OBS credentials; key
  confined to the caller tenant namespace).

- **Method:** `PUT`
  **Path:** `/v1/relay/obs/dir`
  **Auth:** relay
  **Operational use:** OBS dir-marker relay (operator OBS credentials; key
  confined to the caller tenant namespace).

- **Method:** `POST`
  **Path:** `/v1/relay/analysis/tasks`
  **Auth:** relay
  **Operational use:** Analysis-platform submit relay (envelope, IAM
  `X-Auth-Token`).

- **Method:** `GET`
  **Path:** `/v1/relay/analysis/{task_id}`
  **Auth:** relay
  **Operational use:** Analysis task-status relay (envelope, IAM `X-Auth-Token`;
  task id validated).

- **Method:** `GET`
  **Path:** `/v1/relay/analysis/{task_id}/logs`
  **Auth:** relay
  **Operational use:** Analysis task-log relay (envelope, IAM; only the
  `task_name` query key is forwarded).

- **Method:** `POST`
  **Path:** `/v1/relay/analysis/{task_id}/terminate`
  **Auth:** relay
  **Operational use:** Analysis task-terminate relay (envelope, IAM
  `X-Auth-Token`; task id validated).

- **Method:** `GET`
  **Path:** `/v1/relay/spa-faq/{repo_id}`
  **Auth:** relay
  **Operational use:** SPA-FAQ relay (envelope, IAM `X-Auth-Token`; repo id
  validated; proxy-bypass;
  `question`/`page_size`/`page_num` only).

### Native agent capability discovery

`GET /v1/agents` is the canonical preflight for Web and Go consumers. Every
row keeps the stable `slug`, `tool`, `origin`, and `legacy_aliases` fields and
adds a JSON-compatible `capabilities` object:

```json
{
  "streaming": true,
  "interactive": false,
  "report_states": [],
  "artifacts": false,
  "degraded_outcomes": false,
  "attachments": {
    "document_context": {
      "argument": "obs_file_list",
      "extensions": ["pdf", "docx", "pptx", "xls", "xlsx", "msg"],
      "max_file_bytes": 26214400,
      "max_files": 10,
      "max_total_bytes": 52428800
    },
    "datasets": null,
    "expert_forwarding": true
  }
}
```

The current ten-row order is `chat`, `knowledge`, `data`, `review`,
`brief_gene`, `analyst`, `deep_genome`, `research`, `design`, `network`.
`deep_genome` advertises `report_states: ["intermediate", "final"]`,
`artifacts: true`, and `degraded_outcomes: true`; `chat` and `review` are
interactive; `chat`, `knowledge`, `review`, and `brief_gene` are streamable.
`analyst`, `research`, `design`, and `network` advertise
`report_states: ["final"]`, `artifacts: true`, and
`degraded_outcomes: true`; these flags describe the canonical report
projection, not live upstream acceptance.
`data` has no chat-completions alias and must not be added to a stream model
map. Treat unknown capability slugs as unsupported and keep authorization
separate from this metadata.

The attachment channels are exact: Chat, Knowledge, and Review accept
document context; Analyst and Research accept document context plus CSV
datasets; Data, BriefGene, DeepGenome, Design, and Network accept neither.
The complete deterministic golden is
`docs/contracts/agents/capabilities.json` (SHA256
`b13f327b1dd1012ef24936cf3183bd37a19d0e1e8ec3dd7a5115352d0ea492b5`). The
descriptor is a capability preflight, not an authorization grant.

### Locale Ingress And Resume

For `/v1/chat/completions`, `/v1/agents/{agent}/runs`, and
`/v1/query/route`, resolve locale in this order: explicit top-level body
value, first supported `Accept-Language` item, then latest-query inference.
Only `en-US` and `zh-CN` are accepted explicitly. `en` / `en-*` normalize to
`en-US`; `zh` / `zh-*` normalize to `zh-CN`; unsupported header items are
skipped. If no supported header remains, Han characters in the latest query
select `zh-CN`, otherwise `en-US`. An unsupported explicit body value is
`422 unsupported_locale`.

Locale is presentation and prompt context only. It must not be used to
authorize a key or select a tool. The resolved value is persisted with the
run and is inherited by Review resume and Chat/Review A2UI actions. For a
legacy run whose stored locale is null, the resume path infers it from the
stored query and does not trust the resume request's headers.

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
headers are `428` / `400`; a stale revision is `409`. A
`503 memory store unavailable` means the SQLite path is corrupt, inaccessible,
or locked beyond
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

### Streaming failure smoke

Use one valid `stream: true` request and one deliberately invalid request
before enabling a client rollout. Authentication, argument validation,
stream construction, and first-event priming all happen before SSE headers:
the invalid request must be an ordinary JSON error (`400`, `401`, or `429`)
and must not return an empty `text/event-stream`. Once the first AG-UI event
is primed, inject or wait for a backend fault and verify the response has
exactly one `RunError`, does not emit `RunFinished`, and ends with one
`data: [DONE]`. Query `GET /v1/runs/{run_id}` and confirm the row is
`failed` with `partial: true` when a Chat answer had started.

Check service logs using the request id. They may contain the exception class
and source location, but must not contain bearer tokens, credential-bearing
URLs/DSNs, SQL statements, or raw exception text. A client cancellation before
`RunFinished` must settle `failed` without writing a synthetic frame; a
cancellation after `RunFinished` keeps the terminal success. Treat any
duplicate `RunError`, `RunFinished` after an error, missing `[DONE]`, or
unredacted secret as a rollout blocker.

### Streaming cutover checklist (ChatAgent / Instant)

Before flipping Web `bot.stream_enabled` + `VITE_STREAM_ENABLED` on:

1. Confirm Bot build includes streamed-answer persistence for every ordinary
   stream (settled `result.formatted.answer` is real text, not `"[streamed]"`).
1. Coordinate the Web flags so both sides enable streaming together.
1. Smoke: start a streamed Instant chat → refresh history → overlay
   answer matches what the user saw during the stream. Repeat with
   `phyto-knowledge` and `phyto-brief-gene`; Review remains an interactive
   A2UI pause when its stream flag is enabled.
1. Optional: send a very long reply and confirm `truncated: true` on
   `GET /v1/runs/{id}` while the live UI still showed the full text.

### A2UI cutover checklist (ChatAgent / Instant)

Enable `PHYTOMNI_A2UI_ENABLED=1` only after the streaming cutover
above is green (P4-0): ordinary streamed-answer persistence must already
be live so non-A2UI traffic is safe. Additional gates:

1. Coordinate the flag with Web so both sides enable A2UI together.
1. Confirm the Bot safety settings remain at or below the Web contract:
   `A2UI_MAX_BODY_BYTES=65536`, `A2UI_MAX_RESPONSE_BYTES=1048576`,
   `A2UI_MAX_IDENTIFIER_RUNES=256`, `A2UI_MAX_FORM_FIELDS=20`,
   `A2UI_MAX_SCALAR_CHARS=4096`, and `A2UI_MAX_CHOICES=100` (each accepts a
   `PHYTOMNI_` alias). The settings permit lower values only.
1. Web must retain action transport and `run_id` while a run stays
   `input_required`, even after the SSE stream emits `RunFinished` and
   the session `finally` block would normally clear bindings; restore
   from `GET /v1/runs/{id}` when the transport drops.
1. Smoke: streamed confirm query → `phyto.a2ui` frame →
   `GET /v1/runs/{id}` shows `input_required` →
   `POST /v1/runs/{id}/a2ui-actions` with accept → run `succeeded` with
   real `formatted.answer` and `result.a2ui` `status: submitted`.
1. Negative smoke with a temporary scoped API key: send a local JSON body
   larger than 65,536 bytes to `/v1/runs/<paused-run>/a2ui-actions` and
   confirm the unified response is `413` with `error.code=413`; do not use a
   production key or external service. Delete the temporary body and revoke
   the key after the check.

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
     | jq -e '(.supportedInterfaces | length) == 1 and \
       (.supportedInterfaces[0].url | endswith("/a2a"))'
   ```

1. Use a key with the `agents` scope and send a small `SendMessage` smoke with
   `A2A-Version: 1.0`:

   ```bash
   payload='{"jsonrpc":"2.0","id":"smoke-1",'
   payload+=' "method":"SendMessage","params":{"message":'
   payload+=' "messageId":"msg-1","contextId":"smoke",'
   payload+=' "role":"ROLE_USER","parts":[{"text":"Reply with one sentence."}]}}}'
   curl -fsS -X POST "$HOST/a2a" \
     -H "Authorization: Bearer $KEY" \
     -H "A2A-Version: 1.0" \
     -H 'Content-Type: application/a2a+json' \
     -d "$payload"
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

- **Surface:** Memory writes/recall
  **Settings and safe defaults:** `MEMORY_MAX_ITEMS=100`,
  `MEMORY_MAX_CONTENT_BYTES=16384`,
  `MEMORY_MAX_TOTAL_BYTES=1048576`,
  `MEMORY_MAX_RETRIEVAL=20`,
  `MEMORY_GRAPH_MAX_BYTES=65536`
  **Operator symptom when exceeded:** API writes reject the policy violation;
  graph recall is bounded/truncated.

- **Surface:** Interop registry/cache
  **Settings and safe defaults:** `INTEROP_MAX_TARGETS=64`,
  `INTEROP_CACHE_MAX_ENTRIES=256`
  **Operator symptom when exceeded:** An over-sized registry fails closed;
  successful discovery entries evict
  oldest-first.

- **Surface:** A2A projections
  **Settings and safe defaults:** `A2A_MAX_HISTORY_MESSAGES=32`,
  `A2A_MAX_ARTIFACT_BYTES=262144`
  **Operator symptom when exceeded:** `GetTask` history and local answer
  artifacts are capped; the underlying run
  is unchanged.

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

The OBS relay also serves canonical curated gene-example Markdown/PNG objects
through authenticated `GET /v1/relay/obs/object`. Listing is additionally
allowed only for the exact `gene-examples/md/` prefix; the catalog root,
image prefixes, per-gene prefixes, and every catalog mutation remain denied.
The existing tenant and content-addressed shared rules remain unchanged.

`DataAgent` is a synchronous native run: the HTTP layer returns its result
inline with status `200`.

### Authorized DataAgent exact-query root-cause replay

The cDNA incident replay is an operator-only evidence action. The probe has
no query override: it always sends the exact incident query and dialogue ID,
and it refuses to read the API key or open a socket unless both explicit live
flags are set. It writes only request/run/task identifiers, HTTP status,
allowlisted error stage fields, response hash, and optional sequence
length/hash/alphabet metrics. It never writes the query, SQL, sequence,
provider body, credentials, or private paths.

Run it once from an operator-controlled host after approving the target Bot
and output path:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
uv run python scripts/dataagent_root_cause_probe.py \
--base-url "$PHYTOMNI_E2E_BASE_URL" \
--api-key-env PHYTOMNI_E2E_API_KEY \
--output /tmp/dataagent-root-cause-20260724.json
```

The command returns exit `2` when either guard or an input is invalid, exit
`1` when the HTTP request or evidence write fails, and exit `0` after one HTTP
response has been hashed and written, including an HTTP 4xx/5xx response.
Correlate the generated request ID with all six payload-free Bot stage events
before selecting a root-cause branch. A successful probe alone does not prove
the transcript contract, and no DataAgent behavior change is allowed until a
first failing boundary and same-cause regression test are recorded.

`GET /v1/runs` accepts these query parameters beyond the basic set:
`user_id=<other>` requires `X-Service-Token` (returns `403` without
it) and lists any tenant's runs; `dialogue_id=<id>` filters to one
chat-ai conversation thread server-side so a paginated history
loads only the target conversation; `created_after=<iso-8601>` /
`created_before=<iso-8601>` apply inclusive ISO-8601 date bounds;
`debug=true` keeps the full `result.raw` payload on each row. Each
row carries `request_id` / `dialogue_id` / `query` / `tool_name` / `model` /
`answer` alongside the standard fields, sourced from
`result.formatted.answer`.

`POST /v1/runs/{thread_id}/resume` is valid only for owner-scoped
ReviewAgent rows in `input_required`. The body is
`{"approved": bool, "edits": string | null}`. When `A2UI_ENABLED` is on,
the interrupt draft may also carry `a2ui` beside the text summary.
Expect `404` for unknown or foreign runs, `409` for terminal / non-paused
runs, FastAPI `422` for malformed bodies, and `409 no pause point for run` if
the registry row
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
payload, `413` for a request/response over the configured A2UI byte caps,
and `409` for non-paused runs / surface mismatch / missing checkpoint /
duplicate POST after success. The parser rejects duplicate keys, trailing
JSON, untrimmed or oversized identifiers, more than 20 form fields, more than
100 choices, and scalar strings over 4,096 characters before graph entry.
Success settles `succeeded` with `result.formatted.answer` and `result.a2ui`
marked `props.status: submitted`.

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
`assistants` / `batch` / `dataset` / `fine-tune` / `vision` / `user_data`;
any other value returns `422`. Stored under
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

`purpose=dataset` is validated before storage as a nonempty UTF-8 or
UTF-8-BOM comma-delimited CSV with unique nonblank headers and at least one
data row. A `201` response means both the OBS object and the owner-scoped
`user_uploads` metadata row were persisted. If the object write succeeds but
metadata registration fails, the route returns `500 upload_metadata_failed`
and does not advertise the path.

### Attachment Preflight And Orphan Review

Native runs and Expert routing validate attachment paths before invoking the
selected handler. A managed path below `API_UPLOAD_PREFIX` must have a
matching `user_uploads` row owned by the authenticated user. The purpose,
filename extension, byte size, and channel must match the public capability
descriptor. Arbitrary managed-prefix paths and foreign-owner rows are
rejected; do not infer ownership from an OBS key.

Use the following exact limits for registered uploads: 10 files per request,
26,214,400 bytes per file, and 52,428,800 bytes in total. The limits are
inclusive. Duplicate paths are rejected before budget checks, including a
path repeated across `obs_file_list` and `data_list`. Dataset descriptions
must be nonblank. Legacy preconfigured OBS paths in `data_list` are a
separate Analyst/Research policy, are not upload-registry evidence, and are
not automatically migrated.

The stable native/Expert `422` codes are `attachment_not_found`,
`attachment_not_supported`, `attachment_format_unsupported`,
`attachment_duplicate`, `attachment_limit_exceeded`,
`attachment_purpose_mismatch`, and `attachment_description_required`.
Responses do not echo submitted OBS paths. Design and Network keep their
legacy attachment fields only for schema compatibility; nonempty values are
fail-closed during migration.

When investigating a stale upload or registry/object mismatch, use a
read-only owner/operator connection to the database selected by
`API_TASKS_DB_PATH` and bind the cutoff timestamp to the `?` parameter. Keep
the inspection projection limited to:

```sql
SELECT file_id, user_id, obs_path, purpose, byte_size, created_at
FROM user_uploads
WHERE created_at < ?;
```

This query lists registered metadata candidates only. An object created before
metadata registration failure will not have a row and must be correlated
with the request id, OBS listing, and service logs before any action. Do not
delete objects or registry rows automatically from this runbook; cleanup
requires evidence from both the object store and the registry.

## Expert Routing Operations

`POST /v1/query/route` is a constrained Expert-routing endpoint. It accepts
an ordered, non-empty `allowed_tools` list (one to ten unique canonical agent
tool names) and a nullable `forced_tool`. The Web service is the trusted
boundary that derives this allowlist from the authenticated user's
permissions; a browser must not send a self-authorized allowlist directly to
Bot. Preserve list order when forwarding the Web request because the router
offers the tools in that order.

Use this shape when smoke-testing with an `agents`-scoped key:

```bash
curl -fsS -X POST "$HOST/v1/query/route" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_query": "Compare drought tolerance candidates",
    "history": [],
    "obs_file_list": [],
    "dialogue_id": "dialogue-id",
    "allowed_tools": ["ChatAgent", "DataAgent", "AnalystAgent"],
    "forced_tool": "DataAgent"
  }'
```

Before enabling or changing the Web integration, verify that an allowed member
returns the normal `agent.run` envelope and that the resolved agent is the
expected forced member. Also verify these strict failures before rollout:

- Missing, empty, duplicate, unknown, or over-ten `allowed_tools`, and a
  non-member `forced_tool`, return `422`.
- A genuine contract violation -- multiple calls, a malformed call structure
  (for example, no function), a call outside `allowed_tools`, or a call that
  disobeys `forced_tool` -- returns `502` and invokes no agent.
- A model *decline* (no choice or no tool call) is not a violation: when
  `allowed_tools` includes `ChatAgent` the route degrades to a ChatAgent
  dispatch with the original `user_query` injected; otherwise it returns
  `502` with no dispatch. The degrade is opt-in and gated on the trusted
  allowlist.
- Malformed or non-object function arguments are extracted arguments, not a
  malformed call structure; selected-agent schema validation of those arguments
  returns `400`. Absent or insufficient `agents` scope returns `401` / `403`.

Do not mask a genuine *violation* with a ChatAgent fallback, retry by
broadening the allowlist, or treat a browser-supplied list as a permission
grant. (A model decline with `ChatAgent` in the trusted allowlist is the one
sanctioned degrade, not a mask.) Inspect the Web-authenticated allowlist and
the Bot's sanitized router warning, correct the upstream permission or model
contract, then repeat the smoke test.

The stable error mapping used by incident triage is:

- **Condition:** Invalid request body or allowlist
  **HTTP:** `422`
  **`error.code`:** `invalid_request`
  **`error.stage`:** -
  **`retryable`:** `false`

- **Condition:** Unsupported Expert attachment
  **HTTP:** `422`
  **`error.code`:** `attachment_not_supported`
  **`error.stage`:** `attachment_validation`
  **`retryable`:** `false`

- **Condition:** Strict selector contract violation
  **HTTP:** `502`
  **`error.code`:** `routing_contract_violation`
  **`error.stage`:** `routing`
  **`retryable`:** `false`

- **Condition:** Routing provider timeout
  **HTTP:** `504`
  **`error.code`:** `upstream_timeout`
  **`error.stage`:** `routing`
  **`retryable`:** `true`

- **Condition:** Routing provider failure
  **HTTP:** `502`
  **`error.code`:** `routing_upstream_failed`
  **`error.stage`:** `routing`
  **`retryable`:** `true`

- **Condition:** Selected-agent argument validation failure
  **HTTP:** `400`
  **`error.code`:** `selected_agent_invalid_argument`
  **`error.stage`:** `dispatch_validation`
  **`retryable`:** `false`

The `422` attachment case is evaluated before dispatch. Attachments can be
forwarded only to `chat`, `knowledge`, and `review`; do not put
`history`, `allowed_tools`, or `forced_tool` into selected-agent arguments.
The response must retain the normal `agent.run` shape with the resolved slug,
`task_ids`, `result.formatted`, and `result.execution`; `result.raw` is
debug-only. Logs may include the exception class and request id, but never
the query, allowlist, extracted arguments, provider payload, credentials, or
raw exception text.

### Expert dark rollout and rollback

Instant is Chat-only and never uses `/v1/query/route`; a literal `@Agent`
mention remains content. Bot has no `EXPERT_ENABLED` switch. Use this
activation sequence:

1. Bot focused, full, and matrix evidence.
1. Web/Go paired evidence.
1. Staging strict-route evidence.
1. Owner decision.
1. Enable the Web `bot.expert_enabled` flag.
1. Monitor response codes, no-dispatch failures, run rows, and resolved-agent
   slugs.

Rollback is to disable `bot.expert_enabled` at Web, then inspect affected
runs and logs by request id. Do not broaden the allowlist, enable a Bot-side
Expert flag, or route legacy A2A optional selection through the strict route.

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

- **Curated gene-example reads.** The authenticated `relay:obs` catalog is
  read-only and permits only canonical Markdown/PNG objects plus the exact
  `gene-examples/md/` list prefix. Use the following probes with a
  non-production key and a Bot-local endpoint:

  ```bash
  GENE_MD='gene-examples/md/AT1G01010_result.md'
  GENE_IMG='gene-examples/img/AT1G01010/AT1G01010_network.png'

  curl -fsS -G "$BOT_URL/v1/relay/obs/list" \
    -H "Authorization: Bearer $BOT_RELAY_KEY" \
    --data-urlencode 'prefix=gene-examples/md/'

  curl -fsS -G "$BOT_URL/v1/relay/obs/object" \
    -H "Authorization: Bearer $BOT_RELAY_KEY" \
    --data-urlencode "path=$GENE_MD" >/tmp/gene-example.md

  curl -fsS -G "$BOT_URL/v1/relay/obs/object" \
    -H "Authorization: Bearer $BOT_RELAY_KEY" \
    --data-urlencode "path=$GENE_IMG" >/tmp/gene-example.png

  GENE_MD_QUERY='gene-examples%2Fmd%2FAT1G01010_result.md'
  curl -sS -o /dev/null -w '%{http_code}\n' -X PUT \
    "$BOT_URL/v1/relay/obs/object?path=$GENE_MD_QUERY" \
    -H "Authorization: Bearer $BOT_RELAY_KEY" \
    --data-binary 'forbidden'
  ```

  The final probe must return `403`. Do not run it with production
  credentials during Bot-local acceptance. The contract does not claim Web
  forwarding, catalog materialization, browser, staging, or production
  acceptance.

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

- **Live-task reconciliation is process-local.** The in-flight worker registry
  (`runtime/live_tasks.py`) that lets `GetTaskStatus` and
  `GET /v1/runs/{run_id}` tell a live umbrella from a dead one lives in one
  process's memory. Under multiple workers a poll served by a worker that did
  not launch the umbrella reads it as dead and can reconcile a still-running
  run to `failed`. Run the API single-worker (the default) until the registry
  moves to shared storage. The generic `analyst`, `research`, `network`, and
  `design` path returns its initial `202` after persisting an umbrella with
  `task_ids: []`, then submits children in that same process-local worker. A
  restart after reservation can therefore leave a generic umbrella `running`
  with no children; there is no durable queue, long-lived coordinator, or
  automatic recovery for it. DeepGenome has its separate specialized
  coordinator and restart behavior below.

- **E12 checkpointer caveat.** ReviewAgent human-in-the-loop pause points
  live in the local SQLite `checkpoints.db` sibling of `server_tasks.db`.
  Run the API as one replica, or keep `/resume` and `/a2ui-actions`
  traffic pinned to a node that shares the same checkpoint file. A
  different replica can see the run row in `input_required` but miss the
  LangGraph checkpoint and return `409 checkpoint_not_available` with the
  safe message `This input request is no longer available.`.

### Inspect A2UI action claims

The first-uplink audit is stored in `run_a2ui_actions` inside the configured
tasks database. Inspect only identity, outcome, and timestamps; action
payloads and graph state are intentionally not stored in this table:

```bash
sqlite3 "$API_TASKS_DB_PATH" \
  "SELECT run_id, user_id, surface_id, widget, action_id, channel, \
          outcome, claimed_at, completed_at \
     FROM run_a2ui_actions \
    ORDER BY claimed_at DESC LIMIT 100;"
```

For one run, add an owner-scoped predicate without selecting any payload:

```bash
sqlite3 "$API_TASKS_DB_PATH" \
  "SELECT run_id, user_id, surface_id, widget, action_id, channel, \
          outcome, claimed_at, completed_at \
     FROM run_a2ui_actions \
    WHERE run_id = '$RUN_ID' AND user_id = '$USER_ID' \
    ORDER BY claimed_at ASC;"
```

`channel` identifies `a2ui` versus `classic`; `outcome` is `claimed`,
`input_required`, `succeeded`, or `failed`. A completed row is immutable for
the original surface, so a repeated or cross-transport submission returns
the stable `a2ui_action_conflict` response rather than replaying a result.

## Direct GaussDB / BI Safety

The server-side BI path has three independent read-only layers:

- **Application:** parse-based validation accepts exactly one PostgreSQL
  read-only query and walks CTEs/nested nodes before a pool is created.
- **Transaction:** every validated fetch enters
  `transaction(readonly=True)`; transaction setup failures are fail-closed and
  never fall back to an unrestricted fetch.
- **Deployment:** the database credential must use a read-only deployment role
  with connect, schema usage, and select privileges only; it must not create,
  write, or alter data, and `default_transaction_read_only=on` should be set.

Pool return executes `RESET ALL` to clear session settings.
It does not use UNLISTEN because GaussDB does not support it. A reset or
query failure is surfaced
with fixed public text; logs contain only the request correlation id and
exception class, never SQL, DSNs, response bodies, or driver messages.

### Authorized GaussDB live probe

Run the probe only from an operator-controlled host after approving the
target table and column. Both explicit live-run flags are required; the
default (and every offline invocation) refuses before loading credentials.

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run python scripts/gauss_live_probe.py \
  --table <approved_table> --column <approved_column> \
  --environment-class production \
  --output e2e/output/gauss_live_probe.json
```

The statement is a validated zero-row write: it changes no rows, but proves
that the Bot read-only transaction returns SQLSTATE `25006` and the deployed
role returns SQLSTATE `42501` outside that transaction. The probe also checks
`transaction_read_only` and that a one-connection pool clears a marker after
`RESET ALL`. Its JSON evidence contains only the commit label, environment
class, pass/fail checks, and those two SQLSTATEs; it never records a DSN,
SQL statement, identifier, row, or raw exception. Exit `2` means authorization
or input validation failed, exit `1` means a check or output write failed, and
exit `0` means all checks passed.

The role grant, `default_transaction_read_only` setting, denied-write check,
and connection-reuse probe remain external Operations evidence. **External
Pending until the authorized probe**: do not mark production cutover complete
from offline policy, unit tests, or a successful health check alone. Record the
sanitized result, operator, timestamp, and rollback reference through the
authorized deployment change process before enabling customer traffic.

### Authorized eighteen-query compatibility comparison

After an owner supplies an archived old-path manifest, compare the direct
GaussDB seam from an operator-controlled host. The baseline is an external
JSON document with one `queries` record per corpus label:
`{"label": "...", "row_count": 0, "columns": [], "sha256": "<64-hex>"}`.
It must contain every label in `tests/fixtures/gauss_query_corpus.json`
exactly once; the runner rejects missing, duplicate, or extra labels.

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run python scripts/compare_gauss_queries.py \
  --baseline /secure/operator/gauss-query-baseline.json \
  --environment-class production \
  --output e2e/output/gauss_query_comparison.json
```

The runner calls only the hardened direct `gauss_query` seam. It writes commit,
environment class, per-label row counts, sorted column names, SHA-256
fingerprints, and match totals; it never writes SQL, DSNs, result rows, or
driver messages. Exit `2` means the live flags or owner baseline are missing
or invalid (`External Pending`), exit `1` means a query or fingerprint
mismatch, and exit `0` means all eighteen fingerprints match. A missing old
service may use an owner-approved archived manifest, but it does not turn
offline comparison tests into production compatibility evidence.

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

## DeepGenome report operations

DeepGenome is coordinated in-process. The launch reservation atomically writes
the owner run, umbrella task, required BriefGene section, and child tracking
rows; `deep_genome_sections` stores logical sections and
`deep_genome_remote_tasks` stores normalized submitted/polling identities. The
coordinator owns bounded remote polling and persists monotonic
`report_revision` snapshots. HTTP, MCP, and the HTTP-backed CLI are readers of
those snapshots, so a status request never polls the analysis platform.

Owner-scoped list and single-run reads use the canonical `formatted` plus
`execution` result. `execution.report` carries `state`, `degraded`, and
`source_artifact_count`; bounded DeepGenome stage, completeness, revision,
timestamp, progress, and failure count remain under
`formatted.metadata.deep_genome`. `formatted.metadata.report` is only an
additive derived compatibility adapter. Default responses do not expose
`task_results`, `live_status`, private artifact records, or raw payloads.

BriefGene must complete before any optional remote analysis is submitted. While
optional work is pending or partially failed, clients may read the latest
`formatted.answer`; `execution.report.state="final"` is published only after
usable analysis and synthesis. A service restart does not resume the
in-process coordinator.
After stopping the service, the read path settles an orphaned umbrella at the
fixed `workflow interrupted by service restart` boundary and preserves its
last intermediate report. There is no durable worker or automatic cross-host
recovery in 0.1.3.

For Web artifact rendering, accept a report revision only when it is newer than
the last rendered revision for the same umbrella run. Render `formatted.answer`
while `formatted.metadata.deep_genome.stage` is non-terminal; once a final
stage is observed, prefer the answer paired with
`execution.report.state="final"` and do not replace it with later
intermediate text. Show degraded/failure counts as a warning state. Never
fetch child task ids from the browser or use `debug=true` for normal rendering.

### Scientific report and artifact inspection

Use the canonical result blocks when triaging a terminal run:

```bash
curl -fsS \
  -H "Authorization: Bearer ptm_..." \
  "http://127.0.0.1:8080/v1/runs/${RUN_ID}" \
  | jq '.result | {formatted, execution}'
```

`formatted.answer` is the scientific display surface. Inspect
`execution.report`, `execution.artifacts`, `execution.output_dirs`, and
`execution.warnings` for operational state. Do not use `debug=true` for normal
rendering and do not copy `execution.tasks`, local paths, provider payloads, or
raw logs into a ticket intended for a customer.

The producer manifest is `.phytomni-artifacts.json` and must use version `1.0`
with relative POSIX paths. The exact role set is:

| Role                | Report context | Operator meaning                 |
| ------------------- | -------------- | -------------------------------- |
| `scientific_report` | eligible       | Report prose.                    |
| `scientific_table`  | eligible       | Scientific table.                |
| `scientific_text`   | eligible       | Scientific notes or text.        |
| `scientific_figure` | excluded       | Downloadable figure only.        |
| `input`             | excluded       | Input material.                  |
| `execution_log`     | excluded       | Operational log.                 |
| `diagnostic`        | excluded       | Diagnostic or manifest metadata. |
| `unknown`           | excluded       | Unproven producer meaning.       |

Admission is fail-closed: missing or invalid manifests and undeclared objects
become `unknown`, while the manifest itself is `diagnostic`. The report reader
accepts at most 8 eligible text artifacts, rejects any verified object above
32,768 bytes, reads at most 32,768 UTF-8 bytes per object, and caps the total
prompt at 120,000 characters. The order matters because an object must first
pass manifest role admission before any byte budget is spent. Stable warnings
include `artifact_manifest_missing`, `artifact_manifest_invalid`,
`artifact_manifest_path_not_listed`, `report_artifact_size_exceeded`,
`report_artifact_read_failed`, `report_no_scientific_text`, and
`report_synthesis_failed`.

An empty or failed synthesis is still a terminal, non-empty report response:
check `execution.tracking.degraded=true`,
`execution.report.state="degraded"`, and the fixed warning code. Do not treat
that state as a healthy scientific conclusion. Provider names and payloads,
hardware tiers, credentials, private source paths, raw logs, and diagnostic
details must be absent from `formatted.answer`, follow-ups, references, and
scientific metadata. If any appear, stop customer delivery, preserve the run
id for internal triage, and open a projection-contract incident.

Before a rollback or migration, stop the API and make a verified SQLite backup
as shown above. Prepare the DeepGenome tables with the guarded admin command:

```bash
phytomni-task-db prepare-deep-genome-rollback --db "$API_TASKS_DB_PATH"
```

The command refuses persisted nonterminal work by default. Only after an
operator has approved the consequence may the command add
`--mark-nonterminal-failed`; record its count and fixed reason in the change
ticket. Restore the prior wheel or image before restoring a database backup,
then run `/readyz`, `/v1/models`, and one owner-scoped status smoke. Do not
claim production migration, Web/Go acceptance, Gauss role proof, or service
retirement from these offline checks. Record any authorized external evidence
through the deployment change process and keep the rollback reference with the
release record.

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

### Historical Analyst Correlation Requires an L2 Packet

For an incident that may require historical Analyst repair, first produce a
read-only packet. The probe is fixed to the captured Web request id, scopes
every candidate to the supplied owner and `agent == "analyst"`, and matches
only an exact request, dialogue, or run identifier. It never uses query text,
titles, output paths, or fuzzy task metadata. It has no `--apply`, `--write`,
or SQL argument, and every packet sets `write_authorized: false`.

```bash
uv run python scripts/analyst_history_repair_probe.py \
  --db "$API_TASKS_DB_PATH" \
  --owner "$PHYTOMNI_REPAIR_OWNER" \
  --web-request-id bdda4801-3ba9-4692-8d16-ad9807a6674d \
  --output /tmp/analyst-history-repair-packet.json \
  --snapshot-output /tmp/analyst-history-private-snapshot.json
```

Use `--bot-request-id`, `--dialogue-id`, or `--run-id` only when the value is
backed by an independent operator record. The packet status is
`zero_matches`, `unique_match`, or `multiple_matches`; only `unique_match`
contains exact run/task ids and a public-field proposal. The optional private
snapshot is written with mode `0600` outside the repository. Review the hash,
snapshot, reversal proposal, and all three L2 approvals before opening a
separate mutation work order. This probe itself never writes the registry.

The current cDNA incident remains `External Pending`: the Bot request id is
unknown and no owner-approved historical execution has been provided. Do not
infer a correlation from the screenshot query, output directory, or task
title, and do not synthesize a sequence or report while the root-cause gate is
stopped.

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

Check server logs for
`sync run bookkeeping write failed for agent <slug>: <ExceptionClass>` (logged
at WARNING level by `_record_sync_run`). The underlying cause is almost always
a `sqlite3.Error` or `OSError` on the
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
payload='{"model":"phyto-brief-gene","resolve_gene_id":true,'
payload+=' "messages":[{"role":"user","content":"What does '
payload+='AT5G42800 do in Arabidopsis?"}]}'
curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  "$HOST/v1/chat/completions" \
  -d "$payload"

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
  `Literal` enum (`agent_context` / `assistants` / `batch` / `dataset` /
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
in OBS. There is no automatic GC. An object left by a
`upload_metadata_failed` response is not advertised and may have no
`user_uploads` row; use [Attachment Preflight And Orphan
Review](#attachment-preflight-and-orphan-review)
to correlate request, object-store, and registry evidence before any
operator-approved cleanup.

### Startup Failure

Common failures:

- **Error:** `SecretEnvelopeError`
  **Meaning:** Wrong license key, damaged `.env.encrypted`, or an envelope
  sealed from a non-UTF-8 / BOM `.env`.
  **Fix:** Verify `PHYTOMNI_LICENSE_KEY`; if the message names a UTF-8 / BOM
  problem, rebuild the envelope from a UTF-8 (no-BOM) source. Otherwise
  redeliver
  the envelope.

- **Error:** `RuntimeError` resolving environment
  **Meaning:** No plaintext `.env`, no encrypted envelope, no testing mode.
  **Fix:** Provide one supported config source.

- **Error:** `PermissionError` on SQLite path
  **Meaning:** Store directory is not writable.
  **Fix:** Fix permissions or configure absolute store paths.

- **Error:** `OSError: [Errno 98]`
  **Meaning:** Port already bound.
  **Fix:** Free the port or change `API_PORT`.

If `/readyz` returns 200 but authenticated endpoints return 500, inspect
stderr or `journalctl -u phytomni-api` and escalate with the request id from
the response header.
