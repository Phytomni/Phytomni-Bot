# HTTP API

Phytomni-Bot can expose the same agents through an authenticated HTTP API.
The HTTP service runs as a separate process beside the stdio MCP server and
dispatches through the shared MCP invocation path, so MCP behavior stays
unchanged.

## Start the Service

```bash
phytomni-api
python -m mcp_server_phytomni.api.server
```

`ApiConfig` in `config/defaults.py` carries non-secret, env-overridable
settings. SQLite stores are local-only because network filesystems can
deadlock under SQLite WAL. See [Configuration](configuration.md) for the
canonical variable matrix.

- **Setting:** API bind host
  **Env:** `API_HOST`
  **Default:** `127.0.0.1`

- **Setting:** API bind port
  **Env:** `API_PORT`
  **Default:** `8080`

- **Setting:** API key store
  **Env:** `API_KEYS_DB_PATH` / `PHYTOMNI_API_KEYS_DB`
  **Default:** `.cache/phytomni/api_keys.sqlite`

- **Setting:** Runs and tasks store
  **Env:** `API_TASKS_DB_PATH` / `PHYTOMNI_TASKS_DB`
  **Default:** `server_tasks.db`

- **Setting:** Memory CRUD flag
  **Env:** `MEMORY_ENABLED` / `PHYTOMNI_MEMORY_ENABLED`
  **Default:** `false` (disabled)

- **Setting:** Memory store
  **Env:** `MEMORY_DB_PATH` / `PHYTOMNI_MEMORY_DB_PATH`
  **Default:** `.cache/phytomni/memory.sqlite`

- **Setting:** Checkpoint store
  **Env:** beside the runs/tasks store
  **Default:** `checkpoints.db`

- **Setting:** Per-key req/min
  **Env:** `API_RATE_LIMIT_PER_MIN`
  **Default:** `120` (`<= 0` disables)

- **Setting:** Succeeded-run TTL
  **Env:** `API_RUN_TTL_OK_HOURS`
  **Default:** `24`

- **Setting:** Failed-run TTL
  **Env:** `API_RUN_TTL_FAIL_DAYS`
  **Default:** `7`

The runs table and tasks table share one SQLite file so the submit-side
writer and the run-status reader address the same source of truth.
LangGraph pause points use a sibling persistent SQLite checkpointer
(`checkpoints.db`), which lets a ReviewAgent human-approval pause survive an
HTTP API process restart.

## Per-user API Keys

Inbound auth is a per-user key, fully separate from the outbound LLM
`API_KEY`. Keys are stored as PBKDF2-HMAC-SHA256 hashes with a per-key salt.
The plaintext is shown once at creation and is not recoverable.

```bash
phytomni-api-key create --user-id alice --name laptop [--expires-days 90]
phytomni-api-key list   [--user-id alice]
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

See [CLI Reference](cli.md) for the full command reference.

For service-to-service flows (Phytomni-Web Go provisioning per-user keys),
set `API_SERVICE_TOKEN` in the deployment environment and use the
authenticated `/v1/api-keys` HTTP routes documented below in *Endpoints*.

Send the key as either header:

```text
Authorization: Bearer ptm_...
X-API-Key: ptm_...
```

Every response carries an `X-Request-Id`. Errors on native routes use:

```json
{
  "error": {
    "code": "invalid_argument",
    "message": "invalid request",
    "request_id": "...",
    "retryable": false
  }
}
```

Over-budget callers get `429` with `Retry-After`. SSE streaming is
supported only on streaming-capable chat models — `phyto-chat`,
`phyto-knowledge`, and `phyto-brief-gene`; `phyto-review` human-in-
the-loop runs must use non-stream chat or native runs plus
`/v1/runs/{id}/resume`. Every other chat-like model with
`stream: true` returns `400` with a per-model message. See the SSE
Streaming section below.

## Locale

The HTTP API accepts `locale` as a top-level request field on
`/v1/chat/completions`, `/v1/agents/{agent}/runs`, and `/v1/query/route`.
For a native run, the body shape is:

```json
{
  "arguments": {
    "user_query": "What is this rice gene?"
  },
  "locale": "en-US"
}
```

Locale resolution is deterministic:

1. An explicit body value wins and must be exactly `en-US` or `zh-CN`.
1. Otherwise the first supported item in `Accept-Language` is used. `en`
   and `en-*` normalize to `en-US`; `zh` and `zh-*` normalize to `zh-CN`.
   Unsupported header items are skipped.
1. Otherwise the latest user-facing query is inferred as `zh-CN` when it
   contains Han characters, and `en-US` otherwise.

An unsupported explicit value returns `422` with code
`unsupported_locale`. Locale controls generated prose and fixed localized
error messages; it is never an authorization input or a tool-selection
input. The resolved value is stored on the run. Review resume and Chat/Review
A2UI action requests inherit that stored locale; a legacy run with a null
locale backfills it from the stored query instead of trusting resume headers.

Example:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/agents/chat/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Accept-Language: zh-TW, en;q=0.8' \
  -H 'Content-Type: application/json' \
  -d '{
    "arguments": {"user_query": "What is this rice gene?", "obs_file_list": []},
    "locale": "en-US"
  }'
```

## Endpoints

- **Method:** `GET`
  **Path:** `/healthz`
  **Auth:** no
  **Purpose:** Liveness, no dependencies.

- **Method:** `GET`
  **Path:** `/readyz`
  **Auth:** no
  **Purpose:** Readiness, checks local store directories without creating files.

- **Method:** `GET`
  **Path:** `/.well-known/agent-card.json`
  **Auth:** no\*
  **Purpose:** Opt-in A2A v1 Agent Card; route exists only when `A2A_ENABLED=1`.

- **Method:** `POST`
  **Path:** `/a2a`
  **Auth:** yes\*
  **Purpose:** Opt-in A2A v1 JSON-RPC `SendMessage` / `SendStreamingMessage` /
  `GetTask`; requires `A2A-Version: 1.0` and the `agents` scope.

- **Method:** `GET`
  **Path:** `/v1/interop/capabilities`
  **Auth:** yes
  **Purpose:** Opt-in sanitized MCP/A2A capability discovery; route exists only
  when `INTEROP_ENABLED=1`, accepts no query overrides, and
  requires the `agents`
  scope.

- **Method:** `GET`
  **Path:** `/v1/models`
  **Auth:** yes
  **Purpose:** Lists OpenAI-compatible model ids.

- **Method:** `POST`
  **Path:** `/v1/chat/completions`
  **Auth:** yes
  **Purpose:** OpenAI-compatible chat endpoint.

- **Method:** `GET`
  **Path:** `/v1/agents`
  **Auth:** yes
  **Purpose:** Lists native agent-run slugs; each row carries `legacy_aliases`
  and additive `capabilities`.

- **Method:** `POST`
  **Path:** `/v1/agents/{agent}/runs`
  **Auth:** yes
  **Purpose:** Invokes one agent by slug.

- **Method:** `POST`
  **Path:** `/v1/query/route`
  **Auth:** yes
  **Purpose:** Autonomous Expert routing: an LLM selects the agent for a query
  and returns its `agent.run` envelope with the resolved slug.

- **Method:** `GET`
  **Path:** `/v1/memories`
  **Auth:** yes
  **Purpose:** Lists live memory records in the authenticated user's namespace;
  route exists only when `MEMORY_ENABLED=1`.

- **Method:** `POST`
  **Path:** `/v1/memories`
  **Auth:** yes
  **Purpose:** Creates one memory record; `user_id` is taken from the API key
  context and cannot be supplied in the body.

- **Method:** `GET`
  **Path:** `/v1/memories/export`
  **Auth:** yes
  **Purpose:** Exports all live records owned by the authenticated user, subject
  to the per-user item bound; expired or foreign records are
  excluded.

- **Method:** `GET`
  **Path:** `/v1/memories/audit`
  **Auth:** svc
  **Purpose:** Lists digest-only memory mutation records; requires the
  configured service token and exists only when `MEMORY_ENABLED=1`.

- **Method:** `GET`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Purpose:** Returns one live owner-scoped memory record.

- **Method:** `PUT`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Purpose:** Replaces one memory with optimistic concurrency; requires
  `If-Match: <revision>`.

- **Method:** `DELETE`
  **Path:** `/v1/memories/{memory_id}`
  **Auth:** yes
  **Purpose:** Deletes one owner-scoped memory idempotently; an optional
  `If-Match` checks its revision.

- **Method:** `GET`
  **Path:** `/v1/runs/{run_id}`
  **Auth:** yes
  **Purpose:** Returns one owner-isolated run state.

- **Method:** `POST`
  **Path:** `/v1/runs/{run_id}/delivery/retry`
  **Auth:** yes
  **Purpose:** Retries a retryable result-archive publication for an owner-
  isolated run.

- **Method:** `POST`
  **Path:** `/v1/runs/{thread_id}/resume`
  **Auth:** yes
  **Purpose:** Resumes a ReviewAgent run paused at a human approval interrupt.

- **Method:** `POST`
  **Path:** `/v1/runs/{run_id}/a2ui-actions`
  **Auth:** yes
  **Purpose:** Resumes a ChatAgent or ReviewAgent run paused on an A2UI confirm
  surface (`input_required`).

- **Method:** `GET`
  **Path:** `/v1/runs/{run_id}/logs`
  **Auth:** yes
  **Purpose:** Returns reconciled task logs for a run.

- **Method:** `GET`
  **Path:** `/v1/runs`
  **Auth:** yes
  **Purpose:** Lists owner-scoped runs newest-first.

- **Method:** `POST`
  **Path:** `/v1/files`
  **Auth:** `files:delegate` scope
  **Purpose:** Creates or replays one resumable asset and returns its browser
  capability.

- **Method:** `POST`
  **Path:** `/v1/files/{asset_id}/capability`
  **Auth:** `files:delegate` scope
  **Purpose:** Renews a browser capability for an owner assertion.

- **Method:** `HEAD`
  **Path:** `/v1/files/{asset_id}`
  **Auth:** asset capability
  **Purpose:** Returns resumable state in response headers.

- **Method:** `PUT`
  **Path:** `/v1/files/{asset_id}/parts/{part_number}`
  **Auth:** asset capability
  **Purpose:** Streams one exact-length multipart part.

- **Method:** `POST`
  **Path:** `/v1/files/{asset_id}/complete`
  **Auth:** asset capability
  **Purpose:** Completes an asset from its authoritative part registry.

- **Method:** `DELETE`
  **Path:** `/v1/files/{asset_id}`
  **Auth:** asset capability
  **Purpose:** Aborts an unfinished asset and releases its provider session.

- **Method:** `POST`
  **Path:** `/v1/api-keys`
  **Auth:** svc
  **Purpose:** Mints a per-user `ptm_...` API key.

- **Method:** `GET`
  **Path:** `/v1/api-keys`
  **Auth:** svc
  **Purpose:** Lists per-user keys (metadata only); optional `?user_id=` filter.

- **Method:** `DELETE`
  **Path:** `/v1/api-keys/{prefix}`
  **Auth:** svc
  **Purpose:** Revokes the key with the given public prefix.

- **Method:** `GET`
  **Path:** `/v1/relay/audit`
  **Auth:** svc
  **Purpose:** Lists relay audit records (service token); filters by user, key
  prefix, service, status, and time range.

- **Method:** `GET`
  **Path:** `/v1/relay/audit/{request_id}`
  **Auth:** svc
  **Purpose:** Fetches relay audit records by request id (service token).

- **Method:** `GET`
  **Path:** `/v1/relay/healthz`
  **Auth:** no
  **Purpose:** Liveness probe for the relay; no auth, only the relay-enabled
  guard; returns `{"status": "ok"}` when relay is enabled and `404`
  when relay is
  disabled.

- **Method:** `POST`
  **Path:** `/v1/relay/llm/chat/completions`
  **Auth:** relay
  **Purpose:** Chat LLM relay (transparent); injects the operator
  `Authorization: Bearer` key.

- **Method:** `POST`
  **Path:** `/v1/relay/coder/chat/completions`
  **Auth:** relay
  **Purpose:** Coder model relay (transparent); injects the operator coder
  Bearer key.

- **Method:** `POST`
  **Path:** `/v1/relay/embed/embeddings`
  **Auth:** relay
  **Purpose:** Embedding relay (transparent); injects the operator embed Bearer
  key (OpenAI shape, OQ-001).

- **Method:** `POST`
  **Path:** `/v1/relay/retrieve/search`
  **Auth:** relay
  **Purpose:** Knowledge retrieve relay (envelope); no operator credential
  injected.

- **Method:** `POST`
  **Path:** `/v1/relay/rerank/rank`
  **Auth:** relay
  **Purpose:** Knowledge rerank relay (envelope); no operator credential
  injected.

- **Method:** `POST`
  **Path:** `/v1/relay/database/nl2sql`
  **Auth:** relay
  **Purpose:** NL2SQL relay (envelope); injects the operator IAM `X-Auth-Token`.

- **Method:** `POST`
  **Path:** `/v1/relay/bi/query`
  **Auth:** relay
  **Purpose:** BI relay (envelope); server-side-terminated — the operator runs
  `gauss_query` against GaussDB, no credential forwarded.

- **Method:** `GET`
  **Path:** `/v1/relay/obs/object`
  **Auth:** relay
  **Purpose:** OBS object download relay; streams a tenant-namespace-confined
  object (key re-validated to
  `agent_data/{user_data,uploads}/<key user id>/`) or one canonical curated
  gene-example Markdown/PNG object under a response-size budget, via operator
  OBS credentials.

- **Method:** `GET`
  **Path:** `/v1/relay/obs/list`
  **Auth:** relay
  **Purpose:** OBS object list relay; enumerates keys under the caller tenant's
  output root (`agent_data/user_data/<key user id>/`) or exactly
  `gene-examples/md/` via operator OBS credentials.

- **Method:** `PUT`
  **Path:** `/v1/relay/obs/object`
  **Auth:** relay
  **Purpose:** OBS object upload relay; writes the request body at a
  tenant-namespace-confined key via operator OBS credentials. Gene-example
  catalog keys are rejected before any OBS write.

- **Method:** `PUT`
  **Path:** `/v1/relay/obs/dir`
  **Auth:** relay
  **Purpose:** OBS dir-marker relay; creates a zero-byte directory marker at a
  tenant-namespace-confined key via operator OBS credentials. Gene-example
  catalog keys are rejected before any OBS write.

- **Method:** `POST`
  **Path:** `/v1/relay/analysis/tasks`
  **Auth:** relay
  **Purpose:** Analysis-platform submit relay (envelope); injects the operator
  IAM `X-Auth-Token` for the analysis region.

- **Method:** `GET`
  **Path:** `/v1/relay/analysis/{task_id}`
  **Auth:** relay
  **Purpose:** Analysis task-status relay (envelope); validates the task id and
  injects the operator IAM `X-Auth-Token`.

- **Method:** `GET`
  **Path:** `/v1/relay/analysis/{task_id}/logs`
  **Auth:** relay
  **Purpose:** Analysis task-log relay (envelope); injects IAM `X-Auth-Token`
  and forwards only the `task_name` query key.

- **Method:** `POST`
  **Path:** `/v1/relay/analysis/{task_id}/terminate`
  **Auth:** relay
  **Purpose:** Analysis task-terminate relay (envelope); validates the task id
  and injects the operator IAM `X-Auth-Token`.

- **Method:** `GET`
  **Path:** `/v1/relay/spa-faq/{repo_id}`
  **Auth:** relay
  **Purpose:** SPA-FAQ relay (envelope); validates the repo id, injects the
  operator IAM `X-Auth-Token`, forwards only
  `question`/`page_size`/`page_num`,
  and bypasses the host proxy.

`GET /v1/agents` returns one row per registered native slug; each
row carries a `legacy_aliases: list[str]` carrying the historical
Web `tool_name` strings that map onto the slug. The route itself
accepts only canonical slugs; the alias list is metadata so
chat-ai and Phytomni-Web Go can build their own alias→slug
translation table without out-of-band negotiation. Bot-added
agents (`brief_gene`, `design`, `network`) ship an empty list
rather than dropping the key so the shape stays uniform and any
future agent must declare its alias inventory explicitly rather
than silently inherit `[]`.

Each row also carries an additive `capabilities` object. Its stable keys are
`streaming`, `interactive`, `report_states`, `artifacts`, and
`degraded_outcomes`, and `attachments`; `report_states` is always a JSON
list. The attachment descriptor is either `null` or an object with a public
argument name, formats, and exact limits. A representative document
descriptor is:

```json
{
  "argument": "obs_file_list",
  "extensions": ["pdf", "docx", "pptx", "xls", "xlsx", "msg"],
  "max_file_bytes": 26214400,
  "max_files": 10,
  "max_total_bytes": 52428800
}
```

The current attachment matrix is:

| Agent slug    | Document context | CSV datasets | Expert forwarding |
| ------------- | ---------------- | ------------ | ----------------- |
| `chat`        | `obs_file_list`  | no           | yes               |
| `knowledge`   | `obs_file_list`  | no           | yes               |
| `data`        | no               | no           | no                |
| `review`      | `obs_file_list`  | no           | yes               |
| `brief_gene`  | no               | no           | no                |
| `analyst`     | `obs_file_list`  | `data_list`  | no                |
| `deep_genome` | no               | no           | no                |
| `research`    | `obs_file_list`  | `data_list`  | no                |
| `design`      | no               | no           | no                |
| `network`     | no               | no           | no                |

`agent_context` and `document` / legacy `chat_attachment` uploads use the
document channel. `dataset` uploads use the CSV channel, require UTF-8 or
UTF-8-BOM comma-delimited CSV, and are advertised only for Analyst and
Research. Managed dataset descriptions may be supplied once via native
`dataset_description`, generated by one bounded completion call, or fall
back to empty strings after completion failure; arbitrary non-managed path
maps still require nonblank descriptions. The complete deterministic golden
is
[`docs/contracts/agents/capabilities.json`](../contracts/agents/capabilities.json)
with SHA256
`b13f327b1dd1012ef24936cf3183bd37a19d0e1e8ec3dd7a5115352d0ea492b5` for the
current UTF-8 file including its final newline. Consumers must treat this
object as the capability source of truth and fail closed for an unknown slug;
it does not grant permission or change the canonical route name.

## User-scoped memory CRUD (opt-in)

The memory routes are disabled unless `MEMORY_ENABLED=1` (or
`PHYTOMNI_MEMORY_ENABLED=1`) is present when the API app starts. A disabled
deployment does not mount the routes and returns `404`; it also does not open
the configured SQLite path. The store is local-only and should use an
absolute path on a persistent local volume in production.

Every request is authenticated with the `agents` scope. The API key's bound
`user_id` is the only namespace selector: request bodies reject `user_id`,
and list/get/update/delete operations cannot address another user's records.
`GET /v1/memories` supports optional `kind` and `limit` filters; the domain
policy caps retrieval at 20 records by default and excludes expired records.

Create and update bodies have this shape:

```json
{
  "kind": "preference",
  "content": "prefers Arabidopsis",
  "tags": ["profile"],
  "expires_at": null
}
```

`PUT /v1/memories/{memory_id}` requires `If-Match` with the record's positive
integer `revision` (quoted or unquoted). A missing header returns `428`, an
invalid value returns `400`, and a stale revision returns `409`; successful
updates increment the revision. Delete is idempotent and accepts the same
header optionally, returning `deleted: false` for a missing or foreign record.
`GET /v1/memories/export` returns `{"object":"memory.export","data":[...]}`
for the current user's live records. It is a portability read, not an admin
query, and does not expose another user's namespace or expired content.

## Explicit memory lifecycle and boundaries

Memory writes are explicit: `POST`, `PUT`, and `DELETE` are the only public
mutation paths. Chat and Knowledge graphs use a lazy, read-only accessor only
when `MEMORY_ENABLED=1` and an authenticated request user is bound. Graph
recall is newest-first, capped by the policy retrieval count and a 64 KiB
UTF-8 prefix budget; memory content is untrusted reference context, not an
instruction source. There is no autonomous `langmem` writer, embedding store,
or semantic index in this release.

`expires_at` is a per-record TTL. List, get, export, and graph recall exclude
expired rows. The local retention operation `MemoryStore.purge_expired()` may
physically delete expired rows; each such deletion is recorded as a
digest-only `delete` row in `memory_mutation_audit`. The service-token-only
audit endpoint exposes actor, operation, request id, revisions, and SHA-256
digests, never raw content or tags.

The memory store is one local SQLite instance, not a multi-worker or shared
network-filesystem consistency layer. With the flag off, routes return `404`
and agents do not open SQLite. A failed API write returns `503` without a
partial mutation; a graph read degrades to an empty, observable result and
logs only a sanitized error class. Back up and restore this database with
SQLite's `.backup` command while the API is stopped, separately from
`server_tasks.db` and `checkpoints.db`.

## A2A v1 server core (opt-in)

The A2A surface is disabled by default and is additive to the MCP and native
HTTP APIs. Enable it with:

```dotenv
PHYTOMNI_A2A_ENABLED=1
PHYTOMNI_A2A_PUBLIC_BASE_URL=https://bot.example.com
```

`A2A_PUBLIC_BASE_URL` must be an absolute HTTP(S) URL. The public Agent Card is
then available without an API key:

```bash
curl https://bot.example.com/.well-known/agent-card.json
```

The card advertises one JSON-RPC v1 interface at `https://bot.example.com/a2a`,
the ten dispatchable MCP tools as skills, Bearer `agents` authorization, and
`streaming=true` / `pushNotifications=false`. `GetTaskStatus` is not an A2A
skill.

Phase 2 accepts `SendMessage`, `SendStreamingMessage`, and owner-scoped
`GetTask`
JSON-RPC methods. Every request must send
`A2A-Version: 1.0` and an API key with the `agents` scope (scope-less legacy
keys remain all-access). Business failures are returned as HTTP 200 JSON-RPC
error envelopes; authentication, authorization, and rate-limit failures stay
HTTP 401/403/429. Supported message parts are:

- `text`: mapped to the selected tool's `user_query` or `goal_description`;
- `data`: a JSON object merged into the tool arguments.

`raw` and URL parts, scalar/list data values, unknown `metadata.skill_id`
values,
and conflicting structured values are rejected. `metadata.skill_id` is read
from the JSON-RPC request metadata, not from nested message metadata. When it
is absent, the legacy Expert router selects a tool from the text with all
schemas and `tool_choice=auto`; returning no tool preserves the ChatAgent
fallback. This optional A2A bridge is recorded in the
[compatibility register](../ops/bot-compatibility-register.md) and is not
allowed to call the strict `/v1/query/route` contract.

For a synchronous local agent the response task reaches `COMPLETED`; remote
submission agents remain `WORKING` and expose their child `task_ids` in task
metadata. Internal `phase` labels are status-message text only and never
replace the A2A lifecycle state. The answer is a text artifact; references,
tabular data, and formatted metadata are emitted as a data artifact.

```bash
curl -X POST https://bot.example.com/a2a \
  -H 'Authorization: Bearer ptm_...' \
  -H 'A2A-Version: 1.0' \
  -H 'Content-Type: application/a2a+json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "call-1",
    "method": "SendMessage",
    "params": {
      "metadata": {"skill_id": "KnowledgeAgent"},
      "message": {
        "messageId": "msg-1",
        "contextId": "context-1",
        "role": "ROLE_USER",
        "parts": [{"text": "Explain rice flowering evidence."}]
      }
    }
  }'
```

The official Python SDK can resolve the card and send the same non-streaming
request. `ClientConfig(streaming=False)` selects the non-streaming method for
this example:

```python
import httpx
from a2a.client import ClientCallContext, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest

async with httpx.AsyncClient(
    headers={"Authorization": "Bearer ptm_..."}
) as http:
    client = await ClientFactory(
        ClientConfig(streaming=False, httpx_client=http)
    ).create_from_url("https://bot.example.com")
    request = SendMessageRequest(
        message=Message(
            message_id="msg-1",
            context_id="context-1",
            role=Role.ROLE_USER,
            parts=[Part(text="Explain rice flowering evidence.")],
        )
    )
    request.metadata["skill_id"] = "KnowledgeAgent"
    async for response in client.send_message(
        request, context=ClientCallContext()
    ):
        print(response)
```

Set `ClientConfig(streaming=True)` for the same request when the caller wants
the `SendStreamingMessage` SSE wrappers; the SDK still exposes the responses
through the same `send_message` iterator.

`ListTasks`, `CancelTask`, push-notification methods, `SubscribeToTask`, and
`GetExtendedAgentCard` return explicit
unsupported-operation errors until their later implementation phases. With
`A2A_ENABLED=0` (the default), both the card and `/a2a` routes are absent and
return the normal HTTP 404 response; all existing native routes keep their
previous behavior.

For streaming, select `SendStreamingMessage` with the same `params.message`
shape. The response is Server-Sent Events whose JSON-RPC `result` payloads are
`task`, `statusUpdate`, or `artifactUpdate` wrappers. Text is emitted as
incremental `artifactUpdate` chunks (`append=true` after the first chunk and
`lastChunk=true` on the final chunk); references and follow-up questions are
sent once as a terminal data artifact. A disconnected client closes the SSE
generator without changing the existing non-streaming route behavior.

The normal event timeline is:

1. one `task` wrapper in `SUBMITTED` state;
1. zero or more `statusUpdate` wrappers in `WORKING` state, carrying phase and
   progress metadata;
1. incremental text `artifactUpdate` wrappers, followed by one structured
   data artifact when references or follow-up questions exist;
1. one terminal `statusUpdate` (`COMPLETED`, `FAILED`, or
   `INPUT_REQUIRED`).

The stream has no replay cursor. A client disconnect does not resume from the
last artifact or make a new request idempotent: use `GetTask` with the returned
task id to inspect the persisted projection, and only send a same-task resume
when the task is `INPUT_REQUIRED` and its generation matches. Repeating an
initial `SendMessage` without a task id starts a new run; clients that need
application-level deduplication must supply their own request key at a layer
above this protocol facade.

`GetTask` accepts an A2A task id previously returned by this endpoint. The
server resolves it through the authenticated user's run registry, returns the
same task/artifact projection as the send path, and caps returned history to
the requested `historyLength`; unknown and foreign ids both return the
protocol's task-not-found error.

When a Review or A2UI-backed Chat run pauses for input, the task state is
`TASK_STATE_INPUT_REQUIRED` and the task includes one data artifact named
`input-required`. Its payload contains only the resumable `run_id`, a
`generation` token, and the supported input JSON schema; checkpoint internals
are never exposed.

To resume an input-required task, send another `SendMessage` with the same
`taskId` and `contextId`. Put the `generation` from the input artifact and the
approved/edit or form/choice fields in a data part. The server validates the
owner, context, generation, and pause state before calling the shared
`aresume_graph` kernel; stale, repeated, or mismatched resumes are deterministic
JSON-RPC invalid-params errors.

## Outbound interop capability discovery (opt-in)

`GET /v1/interop/capabilities` is a metadata-only discovery surface for
operator-configured external MCP and A2A targets. It is mounted only when
`INTEROP_ENABLED=1` (or `PHYTOMNI_INTEROP_ENABLED=1`) was enabled when the API
application started. It requires an API key with the `agents` scope and the
normal `API_RATE_LIMIT_PER_MIN` budget. With the flag off, the route does not
exist and returns the ordinary `404`; enabling or disabling it requires a
process restart.

The request has no body and accepts no query parameters. URLs, commands, args,
headers, tokens, and credential references are all operator configuration, not
caller input. The endpoint reads the immutable registry and discovery caches;
it never invokes a remote tool, starts an external agent run, or returns an
executable tool/client object.

Successful and partial responses have this shape:

```json
{
  "object": "list",
  "data": [
    {
      "target_id": "mcp-peer",
      "kind": "mcp",
      "remote_name": "search",
      "qualified_name": "mcp-peer__search",
      "description": "Search plant literature",
      "input_schema": {"type": "object", "properties": {}}
    }
  ],
  "errors": [
    {"target_id": "a2a-peer", "kind": "a2a", "code": "discovery_failed"}
  ]
}
```

`data` contains only bounded, deterministic capability DTOs. `errors` is
target-level and stable: one failed target does not hide successful targets,
and it contains only `target_id`, `kind`, and a safe `code`. Endpoint URLs,
stdio commands/args, credential references, headers, tokens, peer payloads,
and exception text are never returned. The same sanitized error shape is used
for registry/discovery failures; a registry that cannot be loaded returns
`503` with the generic `interop registry unavailable` error.

Discovery uses a per-target monotonic TTL and single-flight cache. Successful
metadata is reused until that target's `discovery_ttl_seconds` expires;
concurrent requests share one in-flight discovery. Results containing errors
are deliberately not long-term cached, so a transient failed peer can recover
on the next request. The cache lives in process memory and stores no executable
tools, transport, credential, or peer response. Structured interop events are
safe operational signals only; there is no persistent interop audit database.

The outbound client boundary is separate from the trusted backend client pool:
HTTP targets use no environment proxy, no redirects, and no transparent retry;
credentials are injected only after origin/path/TLS and DNS/IP policy pass.
HTTPS is required unless a target explicitly permits HTTP, and private or
special-use addresses require an explicit private CIDR allowlist. Stdio means
the operator has authorized a fixed absolute local binary; review its path and
arguments as code execution policy. A2A cards are structurally validated and
allowlisted; without a configured JWS key, the service makes no signature
verification claim.

The discovery route never executes a peer. Research and Design delegation is a
separate request-level seam: each native run may opt in with
`interop_mode=auto|required` and operator-registered `interop_targets`; the
default `off` mode remains local-only. See the request policy below for
fallback, failure, and A2A input-required behavior.

## Research/Design outbound delegation (opt-in)

`POST /v1/agents/research/runs` and `POST /v1/agents/design/runs` accept the
same optional controls as the MCP tools. The HTTP layer validates the mode and
forwards only target ids:

```json
{
  "arguments": {
    "user_query": "Summarize the uploaded paper.",
    "data_list": {},
    "obs_file_list": [],
    "interop_mode": "auto",
    "interop_targets": ["mcp-peer", "a2a-peer"]
  }
}
```

`off` never discovers or invokes a peer. `auto` may use one eligible MCP or
A2A capability and continues through the local Analyst path when discovery,
timeout, transport, or evidence collection fails; the result then carries
`formatted.metadata.degraded_interop=true` and a bounded `status="degraded"`
entry in `formatted.metadata.interop`. `required` must receive external
evidence before local submission and returns a failed result when no eligible
peer can provide it. It never turns a local fallback into a pseudo-success.

An A2A `input-required` response pauses the graph before local Analyst
submission. The native run response exposes the bounded pause draft and the
same run resume adapter continues the remote exchange; once completed, local
submission still uses the existing Analyst/OBS path. `metadata.interop` never
contains endpoint URLs, credentials, peer payloads, or task/context
correlation ids; those remain in sanitized `raw.phytomni_state` only when
debug projection is explicitly requested.

`GET /v1/runs` accepts optional `status`, `agent`, `origin`, `limit`,
`offset`, `created_after`, `created_before`, `user_id`, `dialogue_id`,
and `debug` query parameters. `dialogue_id` is an exact-match
server-side `WHERE` predicate so callers can paginate one chat-ai
conversation thread without loading the full owner history.
`created_after` / `created_before` take ISO-8601 strings and are
compared inclusively against the row's `created_at`; ISO-8601 UTC
strings sort lexicographically so the SQL predicate matches
chronological intent without conversion. Filters compose
conjunctively. Each response row carries `dialogue_id`, `query`,
`tool_name`, `model`, and `answer` alongside the standard run
fields; `answer` is sourced from `result.formatted.answer`. The
`debug=true` flag keeps the full `result.raw` block (default mode
strips it, see *Response Projection* below).

DeepGenome rows use the same owner-scoped canonical projection as every other
run. `answer` is sourced from `result.formatted.answer`, preferring the best
nonblank final or intermediate snapshot report. The report contract is carried
by `result.execution.report` (`state`, `degraded`, and
`source_artifact_count`); bounded snapshot progress remains under
`result.formatted.metadata.deep_genome`. A list read never reconciles or probes
concrete remote children. Default mode emits only the public `formatted` and
`execution` blocks; task results, live status, private artifact records, and
raw data remain debug-only compatibility fields.

`result.formatted.metadata.report` is an additive, deprecated compatibility
adapter derived from `result.execution.report`. It contains the same three
report fields and never becomes an independent source of truth. Existing
formatted metadata is preserved after public allowlisting, and legacy rows
without a canonical block are normalized when their bounded snapshot can be
validated.

`GET /v1/runs?user_id=<other-user>` lets an upstream operator list
any tenant's runs. The user key in `Authorization: Bearer ptm_...`
still authenticates the caller for rate-limit + audit, but the
`user_id` parameter is honoured only when a valid service token
arrives in `X-Service-Token: <token>` (the soft-check prefers the
dedicated header so a Bearer-carried user key cannot get confused
for the service token). Missing or wrong service token returns
`403 user_id query parameter requires the service token`. The
owner-only path (no `user_id`) keeps its existing contract.

### Run lifecycle conditions

The public run status is valid only when its durable backing condition is
present:

- **Status:** `input_required`
  **Required durable condition:** Persisted run plus a valid `v1.0` A2UI surface

- **Status:** `running`
  **Required durable condition:** Persisted run, or real accepted tasks plus
  degraded tracking

- **Status:** `succeeded`
  **Required durable condition:** Persisted run plus the canonical result
  projection

- **Status:** `failed`
  **Required durable condition:** Safe error projection with no private payload

Every error response uses the public `error` envelope with `code`, safe
`message`, `request_id`, `stage` when applicable, and `retryable`. Internal
exception text, SQL, credentials, private paths, provider payloads, and model
output are never part of an error response.

`POST /v1/runs/{thread_id}/resume` accepts
`{"approved": bool, "edits": string | null}` for a ReviewAgent run
whose current status is `input_required`. The `thread_id` is the same
value as the Bot `run_id` returned in the interrupt body. Unknown runs
return `404`, terminal or otherwise non-paused runs return `409`,
malformed resume bodies return FastAPI's normal `422`, and a missing
checkpoint returns `409` with `code=checkpoint_not_available`, message
`This input request is no longer available.`, and `stage=resume_checkpoint`.
The checkpoint probe happens before the durable action claim, so this failure
does not consume the input request. Classic `/resume` is independent of the
`A2UI_ENABLED` flag, but it shares the same persistent first-uplink claim as
`/a2ui-actions`; a later transport receives `409` with
`code=a2ui_action_conflict`, message
`This input request has already been handled.`, and `stage=resume_claim`.
If the resumed graph
pauses again, the response repeats
`{"interrupt": {"thread_id", "draft"}, "status": "input_required"}`;
otherwise it settles the run as `succeeded` and returns the normal
`agent.run` result envelope. When `A2UI_ENABLED` is on, HTTP pause
projection nests an optional `a2ui` confirm surface beside the text
draft (`interrupt.draft.draft` carries the human-readable summary;
`interrupt.draft.a2ui` carries the downlink value). The LangGraph
checkpoint still stores only the text draft — projection is registry-only.

`POST /v1/runs/{run_id}/a2ui-actions` accepts the Web action envelope
for a ChatAgent or ReviewAgent run paused on an A2UI surface. Dispatch
keys off `run.agent` (`chat` vs `review`). The route is gated behind
`A2UI_ENABLED` / `PHYTOMNI_A2UI_ENABLED` (default off). Request body:

```json
{
  "surface_id": "<from interrupt draft>",
  "widget": "confirm",
  "action_id": "<client-issued id>",
  "run_id": "<same as path>",
  "payload": {"accepted": true}
}
```

Confirm payloads carry `{"accepted": bool}`; form payloads carry
`{"fields": {...}}` and choice payloads carry
`{"selected": string | string[]}`; form and choice cancel actions send
`{"cancelled": true}`. Form and choice envelopes validate the same
shapes Web already emits. Chat and Review may emit `confirm`, `form`,
or `choice` behind the same `A2UI_ENABLED` flag (widget selection
priority: confirm > form > choice via `select_chat_a2ui_widget`).
Surface props are authored server-side (domain templates → optional
LLM → thin fallback); Review HTTP projection uses the offline author
path (domain + thin, no LLM). Review A2UI resume maps confirm
accept/reject to `{"approved": bool, "edits": null}`, form submit to
`{"approved": true, "fields": {...}, "edits": null}`, choice submit to
`{"approved": true, "selected": ..., "edits": null}`, and form/choice
cancel to `{"approved": false, "cancelled": true, "edits": null}`. The
Review graph does not consume `edits` even when `/resume` accepts them.
Chat and Review A2UI are bounded to **N=2** rounds per run
(`a2ui_round`); a second pause remints a fresh `surface_id`. The path
`run_id` must match `body.run_id` or the call returns `400 run_id mismatch`.

Direct HTTP A2UI parsing is bounded before the resume graph runs. The request
body is capped at 65,536 bytes (both advertised `Content-Length` and streamed
bytes), identifiers are limited to 256 runes and must be nonblank/trimmed,
form actions carry at most 20 fields, scalar strings are at most 4,096
characters, and choice selections are capped at 100 items. Duplicate JSON
keys and trailing JSON are rejected; the raw body is not logged. Successful
and `input_required` responses are serialized under a 1,048,576-byte cap.
Operators may lower these values with the `A2UI_MAX_*` or
`PHYTOMNI_A2UI_MAX_*` settings, but cannot raise them above these ceilings.
Oversized requests or responses use the unified `413` error envelope;
duplicate, malformed, or over-shape actions use the unified `400` envelope.
Web still owns end-user identity and tenant selection; these Bot-side limits
are an additional boundary, not an ownership substitute.

Copyable A2UI downlink / uplink / success / `input_required` / error goldens
live under
[`docs/contracts/a2ui/`](../contracts/a2ui/README.md) for Web and Go
gateway consumers: `chat_confirm`, `review_confirm`, `chat_form`,
`chat_choice`, `review_form`, `review_choice`, plus
`multi_turn/round2_downlink.json` and
`multi_turn/round2_input_required.json` (`sfc-contract-2`). Those fixtures
lock shapes only; this section and the offline HTTP tests remain authoritative
for runtime behavior.

For the reproducible current-SHA focused packet, HTTP body hashes, and the
Bot Ready versus external acceptance boundary, see the [Bot contract
acceptance runbook](../ops/bot-contract-acceptance-runbook.md). Synthetic
HTTP goldens do not close Web, Go, staging, or production acceptance.

| Condition                    | HTTP  | Detail                             |
| ---------------------------- | ----- | ---------------------------------- |
| `A2UI_ENABLED` off           | `403` | `forbidden`, `a2ui disabled`       |
| Unknown or foreign run       | `404` | `run not found: <run_id>`          |
| Path/body `run_id` mismatch  | `400` | `run_id mismatch`                  |
| Invalid widget payload       | `400` | e.g. missing `accepted` on confirm |
| Run not `input_required`     | `409` | `run is not awaiting input`        |
| No open surface on run       | `409` | `no open a2ui surface`             |
| `surface_id` ≠ draft         | `409` | `surface_id mismatch`              |
| Missing LangGraph checkpoint | `409` | `checkpoint_not_available`         |
| Second POST after success    | `409` | `a2ui_action_conflict`             |
| Body above 65,536 bytes      | `413` | `a2ui request body too large`      |
| Response above 1 MiB         | `413` | `a2ui response body too large`     |
| Malformed or over-shape body | `400` | `invalid a2ui action envelope`     |

On success the run settles `succeeded` and the response carries the
normal `agent.run` envelope with `result.formatted.answer` (the real
agent answer, or the short cancel string on Chat reject / form or
choice cancel) plus `result.a2ui` when the pause carried a projected
surface: the prior downlink cloned with `props.status: "submitted"`
and `props.accepted`, `props.cancelled`, `props.fields`, or
`props.selected` when applicable. Both `/resume` and `/a2ui-actions` attach
`result.a2ui`
on success when projection was present. The two uplinks coexist for
Review pauses — send only one per pause round; the first success wins
and the second returns `409`. If the resumed graph pauses again, the
response stays `status: "input_required"` with a fresh `interrupt`
block (a new `surface_id` each round).

The stdio MCP path uses client elicitation for the same ReviewAgent
approval payload. Clients that advertise elicitation support are shown
the draft and return `approved` / `edits`; clients without that
capability gracefully degrade to auto-approval so legacy one-shot calls
keep completing.

E12 single-replica caveat: the checkpointer is local SQLite. Run the HTTP
API as a single replica, or ensure all `/resume` and `/a2ui-actions`
requests for a paused thread land on the same node with the same local
`checkpoints.db`; otherwise a second replica can see the registry row but
miss the pause checkpoint and return `409 no pause point for run`.

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
`web` user, and broadening to multi-tenant delegation would happen
alongside the candidate-B owner-key revival.

For Analyst tasks, reconciliation queries the effective remote task id
(`source_task_id` for a deduplicated caller-owned row, otherwise `task_id`).
When the analysis platform returns ordered `logs[].content` strings, each
per-task payload preserves the provider fields and adds `text` containing
those strings concatenated in array order without an inserted delimiter.
This additive projection also applies to cached payloads written before the
field existed, so Web can consume `task_logs[].text` without a new provider
request.

Consumer impact: this `{run_id, task_ids, task_logs}` shape is NOT a
drop-in replacement for the legacy Web `/query/analyst/update_log`
contract — the old top-level `init_info` / `steps` payload is never
emitted, so Web Go must adapt its reader to walk `task_logs[]` rather
than expecting the lifted fields. Reconcile-on-read is best-effort per
task: when the upstream log service (EIHealth) is unreachable, that
task's reconcile yields no fresh payload (the cached value if one
exists, otherwise the entry is absent) and the request still returns
`200`. A sparse `task_logs` can therefore mask an upstream log-service
outage rather than surfacing it as an error; pass `debug=true` to
inspect the raw per-task payloads when a log looks unexpectedly thin.

The upload protocol is resumable and has separate control and data planes.
The control plane accepts only a trusted service key with the explicit
`files:delegate` scope. The data plane accepts only the opaque Bearer
capability returned for one asset; a normal user key, object key, bucket name,
provider upload id, or cloud credential is not accepted there.

`POST /v1/files` accepts JSON metadata:

```json
{
  "owner_subject": "alice",
  "filename": "report.pdf",
  "content_type_hint": "application/pdf",
  "size_bytes": 524288,
  "purpose": "chat_attachment",
  "idempotency_key": "web-upload-123"
}
```

`purpose` is chosen at create time and is immutable for the asset lifecycle.
Accepted values are `dataset`, `document`, and legacy `chat_attachment`
(effective document partition). The default is `chat_attachment`. Submission
bodies never restate purpose; the owner-scoped resolver recovers it from the
completed registry row. A purpose conflict against an existing idempotent row
returns `409`.

The `201` response contains only `protocol`, `asset_id`, `status`,
`part_size_bytes`, `part_count`, `max_parallel_parts`, `upload_url`, an
opaque `capability`, and the two expiry timestamps. Repeating the same
owner-scoped idempotency key replays the existing asset and capability.
`POST /v1/files/{asset_id}/capability` returns a fresh capability after the
same control-plane service asserts the owner.

The browser data plane uses the capability in `Authorization: Bearer`:

1. `HEAD /v1/files/{asset_id}` reports status and received parts through
   `Upload-*` response headers.
1. `PUT /v1/files/{asset_id}/parts/{part_number}` requires an exact
   `Content-Length` and `X-Phytomni-Part-SHA256`; the body is spooled to a
   bounded temporary file and never buffered as a complete upload.
1. `POST /v1/files/{asset_id}/complete` verifies the authoritative part
   registry and returns the completed `asset_id` descriptor.
1. `DELETE /v1/files/{asset_id}` aborts an unfinished asset.

The default resumable limit is 10 GiB (`API_UPLOAD_V2_MAX_BYTES`) with
128 MiB parts and four recommended parallel parts. Expired sessions are
reconciled by the rate-limited cleanup hook; cleanup is retryable after a
provider abort failure and never exposes provider diagnostics in the public
error body. The old multipart body sent to `POST /v1/files` is not a second
upload protocol and is rejected by request validation before storage.

Example metadata create:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/files \
  -H "Authorization: Bearer ${FILES_DELEGATE_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"owner_subject":"alice","filename":"report.pdf",'\
      '"content_type_hint":"application/pdf","size_bytes":524288,'\
      '"purpose":"chat_attachment","idempotency_key":"web-upload-123"}'
```

Agent requests use the completed asset id, not an OBS path:

```json
{
  "arguments": {
    "user_query": "Summarize the uploaded paper."
  },
  "attachments": [{"asset_id": "file_..."}]
}
```

The resolver checks completion and owner, downloads only into a generated
private run directory when an agent needs local bytes, and projects a safe
owner-scoped legacy `obs_file_list` entry for agents that use the existing
OBS-backed path boundary. Public responses never return an object key or
provider upload id.

## Attachment Invocation Contract

Native runs and Expert routing validate attachments before the selected
handler is called. An `asset_id` returned by the resumable upload protocol is
resolved only when the authenticated attachment owner matches the completed
registry row. When a request asserts `owner_subject`, the caller must hold
the explicit `files:delegate` scope (`403` otherwise). Owner mismatch or a
missing/incomplete asset collapses to a generic `404`. Duplicate asset ids
or conflicting managed references return `409`. Callers cannot turn an
arbitrary OBS path, object key, or incomplete asset into an authenticated
attachment reference.

Native `POST /v1/agents/{agent}/runs` accepts an optional batch-level
`dataset_description` string (maximum 4,000 scalar characters). Purpose is
not repeated at submission time. Analyst and Research project resolved
attachments into legacy channels before dispatch:

- dataset assets become ordered `data_list` entries keyed by managed
  internal references;
- document assets append managed references to `obs_file_list`.

A nonblank `dataset_description` is copied onto every managed dataset entry
and skips model-assisted completion. An omitted or blank description may
trigger one bounded completion call; timeout, invalid output, or provider
failure falls back to managed empty strings so the analysis submission is
not blocked. Arbitrary non-managed path maps remain strict and still require
nonblank descriptions.

Chat (`/v1/chat/completions` and native `chat` runs) remains document-only:
a dataset asset is rejected for the selected Chat agent. Knowledge and
Review stay on the document channel. Expert routing intersects the caller's
authorized tool allowlist with agents that advertise the required attachment
channels; when datasets are present, only authorized Analyst and Research
candidates remain. An explicit unsupported forced-tool or selected agent
returns `attachment_not_supported` without rerouting or dropping assets.

The document channel accepts document/`chat_attachment` metadata with
`pdf`, `docx`, `pptx`, `xls`, `xlsx`, or `msg` filenames for Chat,
Knowledge, Review, Analyst, and Research. The dataset channel accepts
`dataset` metadata only for Analyst and Research and is CSV-only at
invocation validation.

The registered-upload limits are inclusive at the boundary: at most 10
attachments, at most 26,214,400 bytes per attachment, and at most 52,428,800
bytes across one request. Exact repeated asset ids are rejected before
budget evaluation. Legacy preconfigured OBS dataset paths remain a separate
`data_list` policy for Analyst and Research; they do not prove ownership of
a new upload and are not converted into `user_uploads` metadata.

Stable public failure codes for this contract include:

- `403` — missing `files:delegate` when asserting `owner_subject`
- `404` — unknown, foreign, or incomplete asset (generic)
- `409` — duplicate asset id, purpose/idempotency conflict, or state conflict
- `422` — `attachment_not_found`, `attachment_not_supported`,
  `attachment_format_unsupported`, `attachment_duplicate`,
  `attachment_limit_exceeded`, `attachment_purpose_mismatch`, or
  `attachment_description_required`

Public messages never echo the submitted OBS path, managed reference,
owner identity, prompt text, or provider body. Request, conversation-
context, and default/debug projections redact private attachment fields
and exact managed references. Design and Network retain their legacy
attachment fields for schema compatibility, but any nonempty value is
rejected during migration.

Shape/resolver fixture evidence for the delegated mixed Analyst request
lives under
[`docs/contracts/agent-attachments/`](../contracts/agent-attachments/).
That packet is Bot Ready proof for JSON shape and owner-scoped projection
only. Web browser evidence, development object-storage/model/remote-
platform runs, staging, and production activation remain separate
`Needs Verification` evidence and are not closed by this Bot packet.

Native example using a completed asset:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/agents/chat/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{
    "arguments": {
      "user_query": "Summarize the uploaded paper."
    },
    "attachments": [{"asset_id": "file_..."}],
    "locale": "en-US"
  }'
```

`/v1/chat/completions` keeps its existing model attachment gate: the
`attachments` field is resolved only for Chat, Knowledge, and Review models,
and datasets are rejected for Chat. The legacy `obs_file_list` field remains
an internal-compatible input where the selected tool accepts it. Native runs
and Expert routing apply the owner, metadata, duplicate, purpose, format,
description, and budget checks above.

`POST /v1/chat/completions` and `POST /v1/agents/{agent}/runs` accept
an optional `dialogue_id` field that groups runs into one visible
thread on the chat-ai history page. The Bot persists it onto the
`runs` row alongside `query` / `tool_name` / `model`; a missing
`dialogue_id` stays NULL. `dialogue_id` is opaque to the Bot — Web
Go assigns it.

Routes marked **svc** require the service token configured via
`API_SERVICE_TOKEN`, sent as `Authorization: Bearer <token>` or
`X-Service-Token: <token>`. When the env var is unset the routes return
`503` with the safe error envelope `code=unavailable`,
`message=service unavailable`, and `retryable=false`; the internal
configuration detail is redacted. An absent or wrong token returns `401`.
The service token is intentionally separate from `ptm_...` user keys
so a leaked user key cannot escalate to key-issuance scope.

## Relay (Credential-Injecting Proxy)

The relay lets an operator front the outbound leaf-service calls (LLM,
coder, embedding, knowledge retrieve/rerank, NL2SQL, BI, analysis, task)
so a downstream deployment can reach them without holding the operator's
real upstream secrets. The customer calls a `/v1/relay/<service>/...`
route with an issued `ptm_...` key; the relay validates the key, strips
the caller credential, injects the operator's real upstream credential,
forwards to a config-resolved upstream URL, and audits the call.

The whole surface is disabled by default. Set `RELAY_ENABLED=1` (or
`PHYTOMNI_RELAY_ENABLED=1`) to expose it; the flag is re-read on every
request, so flipping it back to `0` stops serving in-flight workers
without a restart, and every relay route returns `404` while disabled.

**Authorization.** Relay routes require a `ptm_...` key whose scopes
include `relay:<service>` or the `relay:*` wildcard. Unlike the agent
routes, an empty-scope (all-access) key is **denied** on the relay
surface, so a legacy convenience key cannot drive the operator's
upstreams. The admission order is `401` (unknown key) → `429` (the
relay-specific per-key budget, separate from the agent budget) → `403`
(missing relay scope). Mint a scoped key with
`phytomni-api-key create --scope relay:llm` (see the CLI reference).

**Two response families.** OpenAI-family routes (`llm` / `coder` /
`embed`) are *transparent*: the upstream status and body are passed
through (so an OpenAI SDK sees its native shapes, including streamed
SSE), the upstream status is read before the streamed response is built
so an upstream `5xx` is never masked as a `200`, and response headers are
reduced to an allowlist (`Content-Type` only) so a reflected operator
credential header cannot leak. Platform-family routes
(`retrieve` / `rerank` / `database` / `analysis` / `bi`) are
*envelope*: a `2xx` body is returned as-is and any upstream error is
mapped to the unified error envelope.

**Per-service upstream credential injected:**

| Service                   | Injected upstream credential                     |
| ------------------------- | ------------------------------------------------ |
| `llm` / `coder` / `embed` | `Authorization: Bearer <operator key>`           |
| `database` / `analysis`   | IAM `X-Auth-Token` (minted via `get_token`)      |
| `bi`                      | none — server-side GaussDB termination           |
| `retrieve` / `rerank`     | none (the upstream is currently unauthenticated) |

**Request and response handling.** The request body is read under a
streaming byte budget (`RELAY_REQUEST_MAX_BYTES`; over-limit returns
`413` without buffering the whole body). The upstream URL is resolved
from server config only — the client query string is never carried onto
the operator-credentialed call. Each key is bounded to
`RELAY_MAX_CONCURRENT_PER_KEY` in-flight forwards (excess returns `503`),
and a forward's total wall-clock lifetime is capped at
`RELAY_TIMEOUT_SECONDS`. Every call is audited best-effort (a failed
audit write never fails a successful relay); audit rows store redacted
request/response bodies. Request copies are capped by
`RELAY_REQUEST_AUDIT_MAX_BYTES`, response copies by
`RELAY_RESPONSE_AUDIT_MAX_BYTES`, and credential-shaped JSON/key-value
fields are replaced with `[REDACTED]`. The public key prefix is retained,
never the key hash or any injected credential header. Query audits with the
service-token `GET /v1/relay/audit` routes.

See *Relay Variables* in `docs/reference/configuration.md` for the knobs and the
*Relay* section of `docs/ops/http-api-runbook.md` for operator
procedures.

### Curated gene-examples catalog

The authenticated `relay:obs` catalog has a deliberately narrow owner-less
read exception:

- `GET /v1/relay/obs/object` permits only
  `gene-examples/md/<GENE>_result.md` and
  `gene-examples/img/<GENE>/<GENE>_<name>.png`, in addition to the existing
  tenant and content-addressed shared namespaces.
- `GET /v1/relay/obs/list` permits the exact `gene-examples/md/` prefix only.
- `PUT /v1/relay/obs/object` and `PUT /v1/relay/obs/dir` reject every
  `gene-examples` key before an OBS operation.

`GENE` is case-sensitive and must begin with `AT`, `GLYMA`, `Os`, `Traes`, or
`Zm`. `RELAY_ENABLED=1`, the `relay:obs` scope, the configured response cap,
streaming cleanup, and metadata-only audit are required exactly as for other
relay operations. See the [copyable contract](../contracts/gene-examples/README.md)
for the producer and Web image-reference boundary.

## OpenAI-compatible Chat

`POST /v1/chat/completions` accepts a `model` value that selects a chat-like
agent:

- `phyto-chat`
- `phyto-knowledge`
- `phyto-review`
- `phyto-brief-gene`

Every chat completion body is an OpenAI ChatCompletion envelope plus two
extra top-level blocks: `formatted` (the normalized display view with
`answer` / `follow_up_questions` / `metadata` / `references` / `tabular`
/ `output_dirs`) and `raw` (the sanitized handler payload carrying
`choices[].message.reasoning_content`, `usage`, `system_fingerprint`,
provider-side `tool_calls` / `refusal`, and any forward-compatible
extension keys). Before the envelope is built, the shared chat boundary
repairs the narrow provider fault where a closed `<think>...</think>`
block contains the final answer tail in `reasoning_content` or at the
front of `content`; clients should treat both `choices[].message.content`
and `raw.choices[].message.content` as the normalized answer field.
Cited-agent answers (`KnowledgeAgent`, `ReviewAgent`,
`BriefGeneAgent`) emit plain markdown with inline `[N]` citation markers
as `message.content` and ship deduplicated citation documents through
`formatted.references`. Each `references[]` entry always carries
`file_id` and `title`. When a bibliographic record exists for a cited
document, the entry additionally carries `au` (authors), `ti` (rich
title), `so` (source/journal), `vl` (volume), `bp`/`ep` (begin/end
page), `py` (year), `di` (DOI id), `dl` (DOI link), and `pm` (PubMed
id). Fields are additive; clients must treat any of the bibliographic
keys as optional and keep rendering from `title` when they are absent.
Every chat completion response also carries a top-level `run_id`: the
Bot-side run identifier minted by the HTTP layer after the completion
is produced. It equals the `run_id` returned by
`GET /v1/runs?dialogue_id=...` for the same dialogue, so clients can
join chat completions against the runs listing without guessing.
`run_id` is distinct from `id` (the OpenAI chat completion id or
provider request id). When the local registry write fails, `run_id` is
`null` and the response additionally carries
`degraded_tracking: true` (see "Chat completion persistence
degradation" in the operations runbook).
The `raw.phytomni_state` namespace carries the
agent's LangGraph intermediate state (retrieved_docs, gene_id,
rewrite_query, research_dimensions, plan, tool_usages, ...) when the
agent populated them. `phyto-brief-gene` rejects a non-empty
`obs_file_list`. The previous top-level duplication of
`follow_up_questions` / `references` / `metadata` was removed — read
them under `formatted` instead.

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-chat","messages":[{"role":"user","content":"Explain C3 photosynthesis."}]}'
```

### SSE Streaming

`POST /v1/chat/completions` accepts an optional boolean `stream`.
Default is `false`. When `true`, the response switches from a single
JSON `chat.completion` envelope to an OpenAI-compatible
`text/event-stream`. Streaming-capable models emit AG-UI event frames:
`phyto-chat` carries provider content deltas in `TextMessage*` frames,
while `phyto-knowledge` / `phyto-brief-gene` add one `StepStarted` per
graph stage and `Custom` frames for `phyto.progress` /
`phyto.references` / `phyto.follow_up` around a one-shot answer. Successful
streams end with a terminating
`data: [DONE]\n\n` so the client closes its `EventSource` on the first
match instead of waiting for the read timeout. Before the response headers
are committed, the API eagerly validates/builds the tool stream and the
first event is primed. A setup or priming failure therefore returns an
ordinary JSON error (with the fixed HTTP status mapping) and settles any
pre-created run row as `failed`, rather than returning an empty SSE body.
The wire framing is AG-UI event data, not provider `chat.completion.chunk`
objects.

Web/Go gateway passthrough, live stream behavior, timeout handling, and
error-equivalence evidence are deployment-owner checks; these offline contract
tests define the Bot wire behavior but do not claim cross-repository
acceptance.

The current server intentionally exposes a narrowed AG-UI vocabulary:
`RunStarted`, `StepStarted`, `TextMessageStart` / `TextMessageContent` /
`TextMessageEnd` (collectively `TextMessage*`), `Custom`, and
`RunFinished`. The richer `ToolCall*`, `Reasoning*`, and `StepFinished`
events from the older event vocabulary are superseded and are not emitted. The
opened-stream lifecycle is also a durable contract: an ordinary producer
failure is projected as exactly one `RunError` with a fixed or redacted
message, does not emit `RunFinished`, and still closes the SSE response with
one `data: [DONE]`. Existing `RunError` frames suppress duplicate errors;
`CancelledError` and generator shutdown propagate for cleanup instead of
being converted into a protocol frame. Credential fragments, private URLs,
SQL statements, and raw unexpected exception text are absent from frames,
public errors, and captured logs.

Streaming is wired on `phyto-chat`, `phyto-knowledge`, and
`phyto-brief-gene`: ChatAgent token-streams provider deltas, while
KnowledgeAgent / BriefGeneAgent drive their compiled graphs through
the `_stream_graph_agent` primitive (stage `StepStarted` frames then a
terminal answer + citations). The ReviewAgent stream capability is
interactive: `phyto-review` with `stream: true` returns
`400` when `A2UI_ENABLED` is off because human-in-the-loop review
pauses resume through the non-stream flow plus `/resume`. When
`A2UI_ENABLED` is on, `phyto-review` with `stream: true` emits a
minimal pause stream: `RunStarted` → one `phyto.a2ui` confirm/form/choice
frame →
`RunFinished` → `data: [DONE]`, settling `input_required` with
`interrupt.draft.a2ui` (no post-resume SSE — resume via `/resume` or
`/a2ui-actions` as for non-stream pauses). Every other chat-like model with
`stream: true` returns `400` with
`streaming is not supported for model <name>` — a clear per-model
signal instead of a silent fallback. The streaming-capable set is
maintained in
`src/mcp_server_phytomni/api/openai_mapping.py:_STREAM_CAPABLE_TOOLS`.

During a graph agent's run, `event: Custom` frames with
`name: "phyto.progress"` carry structured progress ticks interleaved
with the stage events. Each frame's `value` is a `ProgressEvent`
(`kind: "phyto.progress"`, `phase`, `current`, `total`, `detail`)
emitted by graph reduce/section nodes; clients may render a
progress bar or stage label from these ticks before the terminal
`TextMessageContent` arrives.

```json
{
  "type": "Custom",
  "name": "phyto.progress",
  "value": {
    "kind": "phyto.progress",
    "phase": "retrieving",
    "current": 3,
    "total": 8,
    "detail": "gene 3/8"
  }
}
```

`resolve_gene_id=true` + `stream=true` is valid only against
`phyto-brief-gene` (the sole model that is both BriefGene-only-
resolver-eligible and streaming-capable); the resolver runs before
the stream begins. Against `phyto-chat` the BriefGene-only gate
returns `400`; against every other non-streaming model the
streaming-capable gate returns `400`.

```bash
curl -N -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-chat","stream":true,"messages":[{"role":"user",\
"content":"Explain C3 photosynthesis."}]}'
```

Auth, rate-limit, request-id, OBS-file processing, and message
flattening all complete *before* the stream starts, so a
`stream=true` request that fails any precondition surfaces as a
normal JSON error envelope (`401` / `429` / `400`) instead of an
empty `text/event-stream`. After the stream drains, the run record is settled
from the wrapper
`finally` block:

- **ChatAgent (`phyto-chat`)**: `result` is
  `{"formatted": {"answer": "<accumulated text>"}, "raw": null,`
  `"stream": true, "truncated": bool, "partial": bool}`.
  `answer` is the concatenation of every `TextMessageContent` delta,
  soft-capped by `STREAM_ANSWER_MAX_BYTES` / `PHYTOMNI_STREAM_ANSWER_MAX_BYTES`
  (default 1 MiB). The SSE wire stream is never truncated.
  `truncated` is true when the stored blob hit the cap; `partial` is
  true when the run settled `failed` (client disconnect before
  `RunFinished`, or an observed mid-stream `RunError`). A client
  cancellation before `RunFinished` settles failed without attempting to
  write a synthetic frame to the disconnected client; cancellation after
  `RunFinished` preserves the succeeded settlement.
  The client cancellation contract is also enforced for A2UI pause streams.
- **ChatAgent A2UI short-circuit** (`phyto-chat`, `A2UI_ENABLED` on,
  heuristic match): when `select_chat_a2ui_widget(user_query)` returns
  `confirm`, `form`, or `choice` (confirm: `请确认` / `是否确认` /
  `确认是否` / `confirm`; form: `请填写` / `请输入` / `fill in` /
  `please enter`; choice: `请选择` / `二选一` / `choose one` /
  `select one`; priority confirm > form > choice), the stream bypasses
  token deltas and instead emits
  `event: RunStarted`, one `event: Custom` frame with
  `name: "phyto.a2ui"` carrying the downlink value
  (`catalog_version`, `surface_id`, `widget`, `props`), then
  `event: RunFinished` and `data: [DONE]`. The run settles
  `input_required` — not `succeeded` — with
  `result.interrupt.thread_id` equal to `run_id` and
  `result.interrupt.draft.a2ui` holding the open surface. Web must keep
  the action transport and `run_id` while the run stays
  `input_required`, even though the SSE iterator has already emitted
  `RunFinished` and its `finally` block would normally clear session
  bindings; restore from `GET /v1/runs/{id}` when needed. Resume the
  paused graph via `POST /v1/runs/{run_id}/a2ui-actions`. With
  `A2UI_ENABLED` off, or when the heuristic does not match, behaviour
  stays the normal token-stream path above.
- **ReviewAgent A2UI pause stream** (`phyto-review`, `A2UI_ENABLED` on,
  `stream: true`): bypasses stage/progress frames and emits
  `RunStarted` → `phyto.a2ui` confirm → `RunFinished` → `[DONE]`.
  The run settles `input_required` with `interrupt.draft.a2ui`; resume
  through `/resume` or `/a2ui-actions` (no second SSE after resume).
  With `A2UI_ENABLED` off, `stream: true` on `phyto-review` stays `400`.
- **KnowledgeAgent / BriefGeneAgent ordinary graph streams**: settle uses
  the same accumulated-answer shape as ChatAgent, including the UTF-8
  storage cap and `partial` flag. A graph terminal answer is one
  `TextMessageContent` delta, so history still receives the exact markdown
  shown on the wire rather than a `[streamed]` placeholder. The ReviewAgent
  A2UI stream settles its structured `input_required` interrupt separately;
  any ordinary Review stream path uses the same accumulator contract.

Open-stream transport failures (`ConnectError` / `TimeoutException`)
are retried once before raising. Once the first event is primed, any
ordinary mid-stream failure becomes the single sanitized `RunError` described
above; a silent retry would re-emit chunks the client already received and
corrupt the SSE timeline. See
`agents/chat/service.py:MAX_OPEN_STREAM_RETRIES`.

### Resolver flags: `resolve_gene_id` and `resolve_to_id`

The HTTP layer can resolve a free-form `user_query` into the canonical
identifier a downstream agent expects, before invoking the agent. Four
agents support pre-shaping today:

- **Flag:** `resolve_gene_id`
  **Eligible models / agents:** `phyto-brief-gene` (chat path) / `brief_gene` /
  `deep_genome` / `design` slugs
  **Resolved fields:** canonical gene id (+ `species_code` on runs)
  **Metadata keys (on success):** `original_query`, `resolved_gene_id`,
  `resolved_species_code`, `resolve_gene_id:                                 true`

- **Flag:** `resolve_to_id`
  **Eligible models / agents:** `network` slug only
  **Resolved fields:** Trait Ontology id + `species_code`
  **Metadata keys (on success):** `original_query`, `resolved_to_id`,
  `resolved_species_code`, `resolve_to_id: true`

`resolve_gene_id` issues one structured LLM call (json_schema response
format) and returns the single best canonical gene/transcript identifier
(e.g. `Os01g0177400`, `AT5G42800`) alongside the matching 3-letter
`species_code` (e.g. `osa` for Oryza sativa, `ath` for Arabidopsis
thaliana, `zma` for Zea mays). The three gene-id agents share the
BriefGene resolver internally so a customer prompt warm-cached by one
agent reuses the resolution on the others through the shared `~90d`
`phyto_chat` cache. `BriefGene` is the only agent exposed on
`/v1/chat/completions` today, so the chat path keeps requiring
`model="phyto-brief-gene"` for the flag; the native runs path accepts
the flag on any of the three slugs above.

On the `deep_genome` and `design` native runs paths,
`resolve_gene_id` injects **both** fields into the forwarded
arguments — `arguments.gene_id` AND `arguments.species_code` — because
the `DeepGenomeAgent` and `DigitalDesignAgent` input schemas require
both fields. The caller therefore sends only `user_query` +
`resolve_gene_id: true` and the HTTP layer fills in the pair before
the agent's Pydantic schema runs. If the resolver cannot determine a
species from the query, the request returns `400` carrying the
resolver reason rather than falling back to a blank `species_code`
that would Pydantic-fail downstream.

`resolve_to_id` is `network`-only. The resolver injects the customer-
curated Plant Trait Ontology catalog (`config/to_ontology.json`,
CC-BY 4.0, releases/2026-01-14, customer-filtered to 573 ids) into the
LLM user message so the model picks from a closed set; the resolver
also validates the returned id against the catalog before injecting it
into `arguments.to_id`. The same structured LLM call also produces
the `species_code` matching the trait query, and the network branch
injects **both** `arguments.to_id` AND `arguments.species_code`
because `GeneNetworkAgent` requires both. A blank `species_code`
returns `400` rather than falling through. The flag is rejected with
`400` on any other agent slug.

Of the 573 catalog ids, 32 carry `status: deprecated_upstream` because
the upstream PTO release either marks them `is_obsolete: true` (31, no
`replaced_by` / `consider` hint) or omits them entirely (1, `TO:0000139`
"grains per panicle"). The catalog continues to accept those ids so the
customer's existing workflow keeps running, but the resolver emits a
WARNING-level log line through `_LOGGER` in
`mcp_server_phytomni.agents.network.resolve_query` whenever it picks
one. The warning carries the chosen id and the original query so
operators can audit whether the downstream network backend still
returns a meaningful result for the upstream-deprecated trait —
this contract covers the Bot resolver / `arguments.to_id` injection
boundary only, not the network backend's downstream acceptance of
the id, which is opaque to the Bot.

Both flags share the same misuse / failure contract:

- The flag must come with a non-blank `user_query` field in the
  request. Missing or blank `user_query` with the flag on returns
  `400` before any LLM call.
- The flag is rejected with `400` when passed to an ineligible model
  or agent slug.
- Resolver failures (blank input, empty candidates, non-JSON LLM
  output, timeout, hallucinated TO id outside the catalog) return
  `400` carrying the resolver reason in `error.message`; failed
  resolutions never silently fall through to a raw user_query call.
- A blank `species_code` from the LLM returns `400` for the
  `deep_genome`, `design`, and `network` slugs because their agent
  schemas require the field; failed species determination never
  silently falls through to a Pydantic ValidationError. A non-blank
  `species_code` is **not** rejected against a fixed catalog — the
  schema accepts any three-letter string — but one outside the bundled
  species data map is logged at `WARNING` (with the code and the query)
  and the request proceeds, mirroring the resolver's warn-but-accept
  handling of upstream-deprecated TO ids. The mismatched code then
  surfaces at the downstream analysis rather than at the resolver.
- The native runs path pops `resolve_gene_id` / `resolve_to_id` and
  `user_query` from `arguments` before forwarding so each agent's
  Pydantic schema never sees the resolver-flag keys.

```bash
# Chat path — BriefGene only
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-brief-gene","resolve_gene_id":true,"messages":[{"role":"user",\
"content":"What does AT5G42800 do in Arabidopsis?"}]}'

# Native runs — BriefGene (rewrites user_query)
curl -s http://127.0.0.1:8080/v1/agents/brief_gene/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"rice TPR6 function","resolve_gene_id":true}}'

# Native runs — deep_genome (resolver injects both gene_id and species_code)
curl -s http://127.0.0.1:8080/v1/agents/deep_genome/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"tell me about CAB1 in rice","resolve_gene_id":true}}'

# Native runs — network (resolver injects both to_id and species_code)
curl -s http://127.0.0.1:8080/v1/agents/network/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"obs_file_list":[],"user_query":"rice plant height trait","resolve_to_id":true}}'

# Native runs — network with a query that resolves to an
# upstream-deprecated id; the request still succeeds and the
# resolver emits the deprecation WARNING in server logs so the
# operator can audit whether to migrate the trait to a canonical id.
curl -s http://127.0.0.1:8080/v1/agents/network/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"obs_file_list":[],"user_query":"grains per panicle","resolve_to_id":true}}'
```

## Native Agent Runs

Synchronous agents (`chat`, `knowledge`, `data`, `review`, `brief_gene`)
respond `200` with a completed `agent.run`:

```json
{
  "id": "run_id",
  "object": "agent.run",
  "agent": "chat",
  "status": "succeeded",
  "task_ids": [],
  "result": {
    "formatted": {
      "answer": "...",
      "follow_up_questions": [],
      "metadata": {},
      "references": [],
      "tabular": null,
      "output_dirs": []
    },
    "raw": {}
  }
}
```

`result` carries the same `{formatted, raw}` envelope as
`/v1/chat/completions`. `formatted.tabular` is non-null only for
DataAgent (`{"headers": [...], "rows": [...]}`); `formatted.output_dirs`
is non-empty only for DigitalDesign fan-out (the primary path remains
mirrored in `formatted.metadata.output_dir` for single-task consumers).
`raw.phytomni_state` carries the LangGraph intermediate state for
agents that populate it.

### Native conversation context V1 (HTTP-private)

`AgentRunRequest.conversation` is an additive, HTTP-only field on
`POST /v1/agents/{slug}/runs`:

```json
{
  "arguments": {"user_query": "bounded execution input"},
  "conversation": {"schema_version": 1, "...": "ConversationEnvelopeV1"}
}
```

The complete sanitized request and response examples live in
`tests/fixtures/conversation_context/v1.json`. The envelope is validated
before run reservation or agent invocation. Its `mode` must be `expert`,
`requested_agent_id` must equal the canonical tool represented by the URL
slug, and that tool must be present in the ordered `allowed_agent_ids` list.
An unknown slug retains the normal `404`; a valid envelope with
`CONVERSATION_CONTEXT_V1_ENABLED` disabled retains the context-disabled
`404`. The URL selects the native agent directly: this path does not invoke
the Expert router.

All ten canonical native slugs accept the envelope. `chat`, `knowledge`,
`data`, `review`, and `brief_gene` use synchronous adapters and return a
terminal `200` response. `analyst`, `deep_genome`, `research`, `design`, and
`network` use the existing durable submission lifecycle and return the
original accepted `202` response. The route and mode are explicit even when
the execution arguments contain a separate `user_query`: the envelope's
`current_message` is the bounded user turn retained in context, while
`arguments` remains the authority for validated tool inputs and resolver
fields.

A successful response adds one bounded top-level `conversation_context`
object:

```json
{
  "schema_version": 1,
  "turn_id": "42",
  "selected_agent_id": "DataAgent",
  "route_source": "explicit_selection",
  "route_reason_code": "EXPLICIT_SELECTION",
  "base_business_context_version": 3,
  "proposed_business_context_version": 4,
  "last_applied_ledger_cursor": 18,
  "context_truncated": false,
  "context_rebuilt": false,
  "context_degraded": false
}
```

The five synchronous adapters stage terminal metadata after the agent result
is available. An asynchronous turn is accepted only after the existing run
path has durably persisted a non-empty run identity and returned
`status="running"` with HTTP `202`; the Bot then stages the bounded user turn
with an empty metadata delta. No assistant answer, report, table, SQL, task
id, URL, path, credential, or background result is written to business
context. There is no later asynchronous context update when the worker
finishes, fails, is cancelled, or times out.

The first request owns the turn before invocation. A concurrent duplicate
returns `409 conversation_context_turn_in_progress`. A retry for a staged or
committed turn replays the stored result and stage without invoking or
submitting the agent again. The caller acknowledges a staged proposal through
`POST /v1/conversation-context/settle`; settlement advances the context
version, and the existing tombstone path remains the deletion boundary.

Context persistence failures are split by outcome. If preparation or dedupe
storage fails before an agent outcome exists, the request returns a sanitized
retryable `503` with `error.code=conversation_context_unavailable` and
`error.stage=context`, and no agent is called. If staging fails after a valid
synchronous result or accepted asynchronous result, the original `200` or
`202` body is preserved, no false stage is emitted, and the body carries only
`conversation_context_degraded: true`. The client must not resubmit solely
because this marker is present; the accepted run identity and existing
idempotency record remain authoritative.

Without `conversation`, native runs keep their V0 request and response
projection. The private field is absent from the public MCP schemas, and MCP
stdio tools do not accept or emit this envelope. The feature remains dark
until `CONVERSATION_CONTEXT_V1_ENABLED` is enabled by the authorized
operator.

### Scientific and execution result projection

Default HTTP run responses separate scientific content from operational
execution state. The following synchronous shape is complete even when no
report or task exists:

```json
{
  "result": {
    "formatted": {
      "answer": "Scientific result",
      "follow_up_questions": [],
      "references": [],
      "tabular": {},
      "metadata": {}
    },
    "execution": {
      "tracking": {
        "degraded": false
      },
      "warnings": [],
      "tasks": [],
      "artifacts": [],
      "output_dirs": [],
      "report": null,
      "diagnostics": []
    }
  }
}
```

A terminal analyst-class run uses the same envelope and places the report
state beside the scientific answer:

```json
{
  "result": {
    "formatted": {
      "answer": "# Scientific result\n",
      "follow_up_questions": [],
      "references": [],
      "tabular": {},
      "metadata": {
        "report": {
          "state": "final",
          "degraded": false,
          "source_artifact_count": 1
        }
      }
    },
    "execution": {
      "tracking": {
        "degraded": false
      },
      "warnings": [],
      "tasks": [
        {
          "id": "task-123",
          "accepted": true,
          "status": "succeeded"
        }
      ],
      "artifacts": [
        {
          "role": "scientific_report",
          "name": "report.md",
          "media_type": "text/markdown",
          "size_bytes": 2048,
          "downloadable": true,
          "report_context_eligible": true,
          "download_ref": "/obs/public/report.md"
        }
      ],
      "output_dirs": ["/obs/public"],
      "report": {
        "state": "final",
        "degraded": false,
        "source_artifact_count": 1
      },
      "diagnostics": []
    }
  }
}
```

`formatted.answer`, follow-up questions, references, and scientific metadata
are the user-facing scientific surface. `execution` is the only public home
for task ids, output locations, artifact descriptors, warnings, diagnostics,
tracking degradation, and report state. `raw` is omitted by default and is
available only through an explicit authorized debug projection.

The old `result.final_report`, `result.intermediate_report`,
`result.artifacts`, `result.task_results`, `result.live_status`, and
`result.degraded` fields are deprecated compatibility inputs/diagnostic fields;
new producers must write the canonical blocks above. Clients must migrate as
follows:

- **Deprecated field:** `result.final_report` / `result.intermediate_report`
  **Canonical replacement:** `formatted.answer` plus `execution.report.state`
  **Removal condition:** Web/Go consumers render the canonical answer and state.

- **Deprecated field:** `result.artifacts`
  **Canonical replacement:** `execution.artifacts`
  **Removal condition:** Web/Go consumers use public descriptors and download
  references.

- **Deprecated field:** `result.task_results` / `result.live_status`
  **Canonical replacement:** `execution.tasks`, `execution.warnings`, and
  `execution.diagnostics`
  **Removal condition:** Operator diagnostics no longer depend on private child
  rows.

- **Deprecated field:** `result.degraded`
  **Canonical replacement:** `execution.tracking.degraded` and
  `execution.report.degraded`
  **Removal condition:** Clients branch only on the canonical degradation
  signals.

These compatibility fields remain an external migration boundary until
Web/Go consumers provide current acceptance evidence. They are not a reason to
add a second producer path or to copy private values into `formatted`.

### Per-Agent `formatted.metadata` Keys

Default-mode responses include a curated subset of LangGraph
intermediate state in `formatted.metadata` so clients can read the
actually-executed query, plan, or goal list without toggling
`debug=true`. Full intermediate state remains in
`raw.phytomni_state` under debug mode; metadata is a curated subset.

The phrase “task fields” below means the bounded fields emitted by the
shared task formatter: `task_id`, `output_dir`, `compute_resource`, `status`,
and `log_status`, together with the universal outcome fields
`succeeded_count`, `failed_count`, and `failures`. Remote fan-out agents also
expose the sanitized `task_ids` tuple when their formatter supports it; the
value contains only non-empty string task identifiers.

- **Agent:** DataAgent
  **Default-mode `formatted.metadata` keys:** `user_query`, `rewrite_query`,
  `is_rewrite`

- **Agent:** KnowledgeAgent / ReviewAgent / BriefGeneAgent
  **Default-mode `formatted.metadata` keys:** (cited; no extra metadata beyond
  stability note; BriefGeneAgent
  adds `degraded` on a recovered
  literature fault —
  see below)

- **Agent:** AnalystAgent
  **Default-mode `formatted.metadata` keys:** `plan` (≤4 KB), `extracted_tools`,
  `method_context_keys`,
  `plan_retries`, plus task fields

- **Agent:** DeepGenomeAgent
  **Default-mode `formatted.metadata` keys:** `task_id`, `output_dir`,
  `species_code`, `gene_id`,
  `compute_resource`, plus task
  fields

- **Agent:** InSilicoResearchAgent
  **Default-mode `formatted.metadata` keys:** `task_ids`, `goals`, `output_dir`,
  `error`, plus task fields and
  optional `interop` /
  `degraded_interop`

- **Agent:** DigitalDesignAgent
  **Default-mode `formatted.metadata` keys:** `task_ids`, `goal_description`
  (≤256 B), plus task fields,
  `output_dirs`, and optional
  `interop` /
  `degraded_interop`

- **Agent:** GeneNetworkAgent
  **Default-mode `formatted.metadata` keys:** `task_ids`, `goal_description`
  (≤256 B), plus task fields

Text fields exceeding their byte cap are truncated with a marker
pointing to the full document in `raw.phytomni_state.<key>`.

For Research/Design opt-in runs, each `metadata.interop[]` entry contains
only `target_id`, `kind` (`mcp` or `a2a`), `capability`, `status`
(`completed`, `input_required`, `degraded`, or `failed`), and `latency_ms`.
The `degraded_interop` boolean is present only when `auto` continued locally
without external evidence.

### ReviewAgent degraded-mode metadata

ReviewAgent's per-dimension fan-out workers may fail independently
(transient backend errors, rate limits, etc.). On all-success runs,
`formatted.metadata` is empty (`{}`) as it has been. On runs where
one or more dimensions fail, `formatted.metadata` is populated with
the universal failure keys:

- `status`: `"SUCCESS"` / `"PARTIAL"` / `"FAILED"` / `"PENDING"`.
  ReviewAgent does not write `phytomni_state.task_ids` (its fan-out
  workers do not mint remote task ids), so the projection resolves
  `succeeded_count` to `0` on every degraded review run and `status`
  lands at `"FAILED"` even when only one fan-out call failed.
- `succeeded_count` / `failed_count`: integers.
- `failures`: list of `{"task_label", "kind", "message"}` dicts;
  `task_label` is `"<fan_out>:<task_index>"` form (e.g. `"draft:2"`,
  `"retrieve:0"`, `"revised:3"`) for outer-worker failures and
  `"add_query:<dim_idx>:<query_idx>"` form for inner per-query
  failures inside `_feedback_rag`. `message` is redacted before it
  reaches `formatted.metadata` — backend URLs and secret-like
  fragments (`token=`, `Bearer ...`) are replaced with placeholders so
  client metadata never discloses internal endpoints or credentials.
  All emitted log output is scrubbed by the same redactor (a redacting
  log formatter on the package handler, including `logger.exception`
  tracebacks), so the unredacted text survives only in
  `raw.phytomni_state.failures` under debug. `traceback_digest` lives
  only in `raw.phytomni_state.failures`, never in `formatted.metadata`.

Clients should branch on `metadata.get("failed_count", 0) > 0` (rather
than `status == "PARTIAL"`) to detect degraded responses today;
`status` will shift to `"PARTIAL"` if a follow-up wires per-dim
`task_ids` writes from review's fan-out workers. `formatted.answer`
continues to contain the rendered review text in both cases — the
sentinel-coexistence pattern (worker writes BOTH the legacy empty-
string / `"{}"` placeholder AND the `FailureRecord`) lets the original-
draft fallback in `revised_reduce_node` render a complete review
answer even on partial failure.

### BriefGeneAgent literature-degraded metadata

- **BriefGeneAgent** (cited): `degraded` (only on a recovered literature
  retrieve fault) — `{reason: "literature_retrieval", count, labels}`. This is
  status-independent: it never sets `status` / `failures`, so a literature-thin
  answer stays a success. A user-visible `⚠️ Literature retrieval degraded`
  banner also rides `message.content`.

### Artifact and report admission

Terminal report synthesis is producer-role based. The filename extension is
never sufficient to grant scientific meaning. The eight exact manifest roles
are:

- **Role:** `scientific_report`
  **`report_context_eligible`:** `true`
  **Meaning:** Producer-declared report prose.

- **Role:** `scientific_table`
  **`report_context_eligible`:** `true`
  **Meaning:** Producer-declared scientific table.

- **Role:** `scientific_text`
  **`report_context_eligible`:** `true`
  **Meaning:** Producer-declared scientific text or notes.

- **Role:** `scientific_figure`
  **`report_context_eligible`:** `false`
  **Meaning:** Figure or image output; listed but not read into the first report
  prompt.

- **Role:** `input`
  **`report_context_eligible`:** `false`
  **Meaning:** Input material copied or retained by the producer.

- **Role:** `execution_log`
  **`report_context_eligible`:** `false`
  **Meaning:** Operational log; never scientific evidence.

- **Role:** `diagnostic`
  **`report_context_eligible`:** `false`
  **Meaning:** Manifest or diagnostic metadata.

- **Role:** `unknown`
  **`report_context_eligible`:** `false`
  **Meaning:** No proven producer meaning.

The producer writes `.phytomni-artifacts.json` last in each output directory.
It must validate against this bounded schema and list every other output once:

```json
{
  "version": "1.0",
  "artifacts": [
    {
      "path": "tables/gene_summary.csv",
      "role": "scientific_table",
      "media_type": "text/csv"
    }
  ]
}
```

Paths are normalized relative POSIX paths. The manifest rejects absolute,
parent, empty, backslash, duplicate, and control-character paths. A missing or
invalid manifest makes every listed object `unknown`; the manifest object
itself is `diagnostic`. A listed object absent from the manifest is `unknown`,
and a manifest declaration with no listed object produces the fixed
`artifact_manifest_path_not_listed` warning without creating a synthetic
artifact. Unknown objects are downloadable when a safe download reference
exists, but never enter report context.

The public descriptor is deliberately path-minimal:

```json
{
  "role": "scientific_report",
  "name": "report.md",
  "media_type": "text/markdown",
  "size_bytes": 2048,
  "downloadable": true,
  "report_context_eligible": true,
  "download_ref": "/obs/public/report.md"
}
```

It never contains `source_path`, a local mount path, provider payloads, or
credentials. Report admission applies limits in this order: validate the
manifest, enumerate actual objects and retain their verified byte sizes,
filter to the three eligible roles, keep at most 8 text artifacts, reject an
artifact over 32,768 bytes, read at most 32,768 UTF-8 bytes per artifact, then
cap the combined prompt at 120,000 characters. Empty, unreadable, or invalid
text is skipped with a stable execution warning; it is not copied into the
scientific answer.

If no eligible scientific text remains, the deterministic fallback has
`execution.report.state="degraded"`,
`execution.report.source_artifact_count=0`, and warning
`report_no_scientific_text`. If reading or synthesis fails, the fallback keeps
the report non-empty, sets `execution.tracking.degraded=true`, uses
`execution.report.state="degraded"`, and emits only fixed warning codes such
as `report_artifact_read_failed` or `report_synthesis_failed`. Provider error
text, private paths, raw logs, diagnostic details, and credential-shaped
fragments never enter the scientific surface.

Remote agents (`analyst`, `deep_genome`, `research`, `design`, `network`)
respond `202` with `status: "running"`. The initial accepted response for
`analyst`, `research`, `design`, and `network` contains a persisted umbrella
`run_id` and `task_ids: []`: semantic resolution and child submission continue
in one process-local detached worker, so no child identity is fabricated before
acceptance. Poll `/v1/runs/{run_id}` for live status; the owner-scoped GET
reads the local run/snapshot store and does not poll the remote analysis
platform. After reservation, a service restart can leave one of these generic
umbrellas `running` with no children; there is no durable queue, long-lived
coordinator, or automatic worker recovery for that boundary.

Once a generic worker records accepted children, the umbrella remains
`running` while the existing task reconciler owns terminal settlement. This is
separate from Deep Genome's specialized in-process coordinator and its report
snapshots described below.

The submit response is a submission acknowledgement, not a completed report.
Use `GetTaskStatus` or `GET /v1/runs/{run_id}` for one non-blocking lookup. A
succeeded analyst-class task exposes its final report through
`result.formatted.answer` with `result.execution.report.state="final"`;
public artifact descriptors and output directories are siblings under
`result.execution`. Offline mocks validate the shape; this does not prove live
backend acceptance.

`deep_genome` runs the whole report workflow in-process in the background, so
unlike the generic background submission workers its terminal product is a
local markdown report rather than an upstream-platform artifact. The
coordinator persists a public snapshot after BriefGene and after each optional
analysis transition. While the run is active, `GetTaskStatus.formatted.answer`
and
the `answer` field of `GET /v1/runs/{run_id}` select the latest nonblank
intermediate report. After successful synthesis, the same answer is paired
with `result.execution.report.state="final"`. Failed umbrellas retain their
last usable answer and expose a degraded execution report; default responses
do not emit snapshot fields such as `final_report` or `intermediate_report`.

The DeepGenome snapshot fields are shared across MCP, HTTP, and the HTTP-backed
CLI: `report_stage`, `report_completeness`, monotonic `report_revision`, UTC
`report_updated_at`, ordered `progress` counts, `degraded`, sanitized
`degraded_reason`, and fixed-message `failures`. Optional analysis rows may
fail independently. BriefGene failure fails the umbrella; after BriefGene
succeeds, partial analysis failure is represented in the snapshot and does not
hide the best report. A final report is published only after synthesis succeeds.

Artifact-oriented consumers should read `result.execution.report` and retain
the last accepted DeepGenome revision from
`result.formatted.metadata.deep_genome`.
Ignore stale or equal revisions, render `formatted.answer` while
`stage=intermediate`, and switch permanently to the answer paired with
`execution.report.state="final"` when `stage=final`. `degraded=true` and
`failure_count` are warning metadata, not permission to label a partial report
complete. `formatted.metadata.report` is retained only as a derived
compatibility adapter.

### DeepGenome persistence and restart boundary

The DeepGenome launch is an atomic local reservation. Before the background
coordinator is scheduled, one transaction creates the owner `runs` row, the
umbrella `tasks` row, and the required BriefGene section. Logical sections are
stored in `deep_genome_sections`; concrete remote submissions, including both
the accepted caller id and effective polling id, are stored in
`deep_genome_remote_tasks`. The coordinator-owned polling loop is bounded by
the configured local and remote deadlines and is the only component allowed to
poll the analysis platform. The HTTP GET path, MCP `GetTaskStatus`, and the
HTTP-backed CLI read the persisted snapshot instead of issuing remote probes.

Each accepted transition updates the snapshot with a monotonic
`report_revision`. The best intermediate or final text is therefore available
through `formatted.answer` after BriefGene and after each optional analysis
transition; `execution.report.state="final"` appears only after a usable
analysis result and successful final synthesis. BriefGene failure is terminal
with no report and zero remote submissions. Optional failures remain isolated
and are reported through `execution.report.degraded`, bounded counts, and fixed
warning messages while the best answer remains visible.

The coordinator is intentionally in-process. A service restart does not
resume after process restart; the read path marks an orphaned nonterminal
umbrella failed with `workflow interrupted by service restart` while retaining
the last intermediate snapshot. This release makes no durable-worker,
production-migration, live-acceptance, or Web/Go-completion claim. Those
deployment and integration checks are separately owned and are not closed by
the Bot-local documentation or test suite.

A `deep_genome` report whose optional mounted sub-analysis degraded mid-run
(its evolution or digital-design step) can still settle as `succeeded` while
flagging the gap through machine-readable keys —
`GetTaskStatus` exposes
`formatted.metadata.deep_genome.degraded` (bool) and
`execution.report.degraded` (bool). The bounded snapshot stage, revision,
progress, and failure count remain under
`formatted.metadata.deep_genome`; the report state is under
`execution.report`. Healthy rows read `execution.tracking.degraded: false`.

BriefGene is required before any remote analysis launch. A BriefGene failure
therefore fails the DeepGenome workflow with the fixed public error
`brief gene profile failed`, produces no intermediate or final report, and
submits zero remote analysis jobs; it is not represented as a degraded
success or a visible fallback banner.

For terminal remote analyst-class runs (`analyst`, `research`, `design`,
and `network`), a successful response exposes the scientific answer in
`result.formatted.answer`, `execution.report.state="final"`, and any safe
artifact descriptors in `result.execution.artifacts`. The report synthesizer
admits only the three scientific manifest roles and applies the caps in
*Artifact and report admission* above. Binary artifacts, PDFs, xlsx files,
and images remain downloadable descriptors when they are not valid report
context.

Report synthesis degradation does not change a successfully completed
remote analysis run to `failed`; clients should render the non-empty fallback
answer and expose `execution.tracking.degraded` and
`execution.report.degraded`.

Assembly is best-effort: an OBS listing failure logs a warning and
leaves `execution.artifacts` without a download reference (the run still
settles as terminal). The single-task `GetTaskStatus` surface does not run
run-level assembly, so its descriptors may have `downloadable=false`. A
succeeded run without downloadable descriptors is therefore an accepted
terminal shape, not an error signal; clients should use the safe
`execution.output_dirs` boundary when an operator-approved download is
needed.

### Remote agent edge cases: `id: null` / `task_ids: []`

A `202` body can legitimately return `id: null` with `task_ids: []` for
two reasons. Both produce the same identity-empty shape, so clients
distinguish them through the extra signals described here:

- **Cause:** Healthy submission
  **`id`:** `str`
  **`task_ids`:** `[…]`
  **Body extras:** —
  **`result` extras:** per-agent payload
  **Client follow-up:** Poll `/v1/runs/{id}` for status.

- **Cause:** Local registry write failed
  **`id`:** `null`
  **`task_ids`:** `[]`
  **Body extras:** `degraded_tracking: true` at the body top level
  **`result` extras:** per-agent payload (the remote submission did succeed)
  **Client follow-up:** Treat the remote run as in-flight but not locally
  tracked; operators should reconcile from the upstream
  system.

The `degraded_tracking: true` body field is added only when the local
SQLite chokepoint (`runtime.submit_recorder.record_submitted_task`) hit
a `sqlite3.Error` / `OSError` while writing the `runs` and `tasks`
rows after the remote platform already accepted the submission. The
remote task is alive upstream, but `GET /v1/runs/{run_id}` will return
`404` until the registry write succeeds (a manual reconcile from the
upstream platform is the recovery path). The chokepoint also writes
the full traceback through `logger.exception` so operators see the
underlying SQLite or OS error in logs.

The sync chat path (`/v1/chat/completions`) also emits
`degraded_tracking: true` with `run_id: null` when its own registry
write (`_record_sync_run`) fails. The completion itself still returns
`200` (bookkeeping must never block a successful answer); the flag
lets clients detect that the run row is absent and `GET /v1/runs`
will not replay this call.

`degraded_tracking` covers the submit-time registry write only. The
later background finalization writes a `deep_genome` run makes — the
terminal status update, the assembled `final_report`, and any
`degraded_reason` — are best-effort: a `sqlite3.Error` / `OSError`
there is logged and swallowed (never raised) and is **not** surfaced as
a distinct client signal. A lost terminal status write no longer strands
the poll in flight, though: `reconcile_task` self-heals a `deep_genome`
umbrella at read time — a row still carrying a `final_report` surfaces as
`succeeded`, and one whose background task is no longer live and produced
no report surfaces as `failed` — so a polling client converges on a
terminal status without an operator reconcile. The one residue is a run
that finished but lost *both* its report write and its status write: with
no report and a dead task it heals to `failed` rather than `succeeded`,
and operators recover the true outcome from the warning log.

Analysis submissions (both the top-level analyst path and the
`submit_analyst_via_subgraph` seam that design / network / research /
deep_genome / environment / evolution funnel through) deduplicate on a
content fingerprint over `goal_description`, `data_list`, and
`obs_file_list`. Results are written to the tenant-neutral key
`agent_data/shared/<fingerprint>/output/`. A fingerprint hit mints a
fresh caller-owned task id (stored in the local `tasks` table with
`source_task_id` pointing at the prior tenant's remote task id), records
the caller's own run, and returns the caller's own `id` and `task_ids` at
HTTP 202 — exactly the same shape as a fresh submission. The
`source_task_id` is used server-side only by `reconcile_task` to probe
live status; it is never returned to the client. A run's `task_ids` may
therefore reference a task originally launched by an earlier run (possibly
from another tenant), but the reconciliation path is unchanged: the caller
polls their own run id and reaches the shared result at the neutral output
path.

```bash
curl -s -X POST http://127.0.0.1:8080/v1/agents/chat/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"Explain C3 photosynthesis.","obs_file_list":[]}}'

curl -s -X POST http://127.0.0.1:8080/v1/agents/analyst/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"goal_description":"...","data_list":{},"obs_file_list":[]}}'

curl -s "http://127.0.0.1:8080/v1/runs/<run-id>" \
  -H "Authorization: Bearer ptm_..."

curl -s "http://127.0.0.1:8080/v1/runs?status=succeeded&limit=20" \
  -H "Authorization: Bearer ptm_..."
```

Unknown run ids and runs owned by another caller collapse to one `404`
envelope so callers cannot enumerate other users' run ids.

## Expert Routing

`POST /v1/query/route` runs autonomous routing: an LLM selects the best
MCP agent for a natural-language query, the selected agent runs
**in-process**, and the response is the **same `agent.run` envelope** as
`POST /v1/agents/{agent}/runs` — with `agent` set to the **resolved
slug** (e.g. `"knowledge"`), never `"expert"`. The selection step reuses
the operator's main conversation model, so no extra credentials are
required.

Request body:

```json
{
  "user_query": "string (required)",
  "history": [{"role": "user|assistant", "content": "string"}],
  "obs_file_list": ["/obs/phytomni/..."],
  "dialogue_id": "string | null",
  "locale": "en-US | zh-CN | null",
  "allowed_tools": ["ChatAgent", "DataAgent", "AnalystAgent"],
  "forced_tool": "DataAgent"
}
```

- `allowed_tools` is required, ordered, non-empty, and contains at most ten
  unique canonical agent tool names. The complete canonical set is
  `ChatAgent`, `KnowledgeAgent`, `DataAgent`, `AnalystAgent`, `ReviewAgent`,
  `BriefGeneAgent`, `DeepGenomeAgent`, `InSilicoResearchAgent`,
  `DigitalDesignAgent`, and `GeneNetworkAgent`; `GetTaskStatus` is excluded.
  The router receives and offers tools in the caller's exact order. This
  allowlist is trusted only when it originates at the authenticated
  Web-service boundary; do not accept it directly from a browser as an
  authorization decision.
- `forced_tool` is nullable. When present, it must be a member of
  `allowed_tools`; it pins the routing model to that canonical tool.
- The request model uses `extra="forbid"`; unknown body keys are rejected with
  `422` rather than forwarded to the selector or selected agent.
- `history` is routing context only; it is never forwarded to the
  dispatched agent.
- `obs_file_list` is injected into the selected tool's arguments only when
  that tool accepts attachments (`chat` / `knowledge` / `review`); the LLM
  fills every other argument from the tool's schema.
- A sync agent returns `200` with `status="succeeded"`; a remote agent
  returns `202` with `status="running"` plus `task_ids`, exactly like the
  native runs path (poll `GET /v1/runs/{id}`).

When the optional private `conversation` envelope is present, the same V1
context lifecycle is applied after selection. Explicit native selection
keeps `route_source=explicit_selection`; autonomous selection keeps the
router-derived source and reason code. Synchronous selections stage after
the terminal result, while asynchronous selections stage only after the
existing `202` run is durably accepted. Without the envelope, this route
retains its V0 behavior. See the native-run contract above and the canonical
fixture at `tests/fixtures/conversation_context/v1.json` for the bounded
response shapes.

Invalid allowlists (missing, empty, over ten entries, duplicate, or unknown
canonical names), a non-member `forced_tool`, and unknown body keys are
rejected with `422`. Routing is strict on genuine contract violations:
multiple calls, a malformed call structure (for example, no function), a
tool outside the allowlist, or failure to honor `forced_tool` fails with
`502` and dispatches no agent. A model *decline* (no choice or no tool
call) is treated separately: it means the turn is plain chat, so when the
caller's `allowed_tools` includes `ChatAgent` the route degrades to a
ChatAgent dispatch with the original `user_query` injected; when the caller
did not authorize `ChatAgent` the decline stays a `502` with no dispatch.
The chat-degrade is opt-in and gated on the trusted allowlist, so a caller
that scoped chat out never has it dispatched on its behalf. Separately,
malformed or non-object function arguments are treated as extracted
arguments and then validated against the selected agent schema; that
validation failure returns `400`. Missing or insufficient scope returns
`401` / `403`.

The stable public error mappings for a validly authenticated request are:

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

Public messages are fixed and localized by `locale` / `Accept-Language`; they
never echo the query, allowlist, extracted arguments, provider payload, or
credentials. Strict failures have no dispatch and no run row. A successful
response keeps the native shape: `object="agent.run"`, the resolved agent
slug, `status`, `task_ids`, and `result.formatted` plus `result.execution`;
`result.raw` is debug-only.

Instant is Chat-only and does not call `/v1/query/route`. A literal `@Agent`
mention remains message content. Expert activation is owned outside Bot;
keep Web `bot.expert_enabled=false` until the external paired acceptance
gate is complete.

Known limitations (v1): the four structured-input agents (`analyst`,
`deep_genome`, `design`, `network`) receive best-effort arguments
extracted by the routing model — `data_list` may be incomplete and a
gene / species / Trait-Ontology id may be guessed — and obs attachments
reach only `chat` / `knowledge` / `review`. Invalid extraction surfaces a
`400` rather than a silent wrong answer.

```bash
curl -s -X POST http://127.0.0.1:8080/v1/query/route \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{
    "user_query": "Compare drought tolerance candidates",
    "history": [],
    "obs_file_list": [],
    "dialogue_id": "dialogue-id",
    "allowed_tools": ["ChatAgent", "DataAgent", "AnalystAgent"],
    "forced_tool": "DataAgent"
  }'
```

## Response Projection

By default, HTTP API responses strip debug-only fields to reduce
payload volume. A BriefGene response drops from ~705 KB to ~5 KB.

**`debug` flag**: pass `"debug": true` in the request body (POST
endpoints) or `?debug=true` as a query parameter (GET endpoints) to
receive the full payload including `raw`, provider extensions, and
retrieved documents.

**`PHYTOMNI_DEBUG=1`** environment variable forces full payloads
regardless of the per-request flag.

### `/v1/chat/completions` default mode

Removed: `raw`, `phytomni_state`, `system_fingerprint`, `service_tier`,
`prompt_logprobs`, `choices[].message.doc_list`,
`choices[].message.total`, `choices[].message.follow_up_questions`,
null provider fields (`refusal`, `annotations`, `audio`,
`function_call`), and `formatted.answer`.

`choices[].message.content` is replaced with the normalized
`formatted.answer` (using `[N]` citation format consistent with
`formatted.references`).

Kept: `id`, `object`, `created`, `model`, `choices` (with `role`,
`content`, `reasoning_content`, `tool_calls`, `finish_reason`,
`index`), `usage` (3 token fields), `formatted` (without `answer`),
`run_id`, and `degraded_tracking` (when present).

### `/v1/agents/{agent}/runs` and `/v1/runs` default mode

Removed: `raw` from `result`.

Kept: `formatted` (all fields including `answer`), `id`, `object`,
`agent`, `status`, `task_ids`, and `degraded_tracking` when the
submit chokepoint hit a local registry write failure (see "Remote
agent edge cases" above).

## Polling

A remote run typically takes minutes to finish. Poll `/v1/runs/{run_id}`
with exponential backoff bounded between `2s` and `30s`, for example
`2s, 4s, 8s, 16s, 30s, 30s`, until `status` flips to `succeeded` or
`failed`.

The endpoint is cheap after terminal state because the cached `result_json`
is returned without re-polling the analysis platform.

## Analyst Dedup and Content-addressed Storage

Analysis submissions use a content-addressed store. The fingerprint is computed
over `goal_description`, `data_list`, and `obs_file_list` (never the caller's
identity), and results are written to the tenant-neutral path
`agent_data/shared/<fingerprint>/output/`. A fingerprint hit always mints a
fresh caller-owned run and task id rather than returning a prior submitter's
ids. The `(id=null, task_ids=[])` body shape no longer arises from a dedup
hit; it occurs only when the local registry write fails (see the
`degraded_tracking` case above).

## Retention and Correlation

A terminal run row carries an `expires_at` based on:

- `API_RUN_TTL_OK_HOURS` for `succeeded`, default 24 hours.
- `API_RUN_TTL_FAIL_DAYS` for `failed`, default 7 days.

The next API write or list call past that timestamp deletes the run row plus
child task rows in one manual cascade. A non-terminal run carries no TTL;
reconcile-on-read keeps it visible until it finishes.

Every response carries an `X-Request-Id` header that is also surfaced in the
error envelope, so `429` and `5xx` responses can be joined back to server
logs.

The MCP stdio server remains `python -m mcp_server_phytomni.server` and is
unaffected by HTTP run-registry writes.
