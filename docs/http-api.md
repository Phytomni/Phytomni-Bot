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

| Setting              | Env                                         | Default                           |
| -------------------- | ------------------------------------------- | --------------------------------- |
| API bind host        | `API_HOST`                                  | `127.0.0.1`                       |
| API bind port        | `API_PORT`                                  | `8080`                            |
| API key store        | `API_KEYS_DB_PATH` / `PHYTOMNI_API_KEYS_DB` | `.cache/phytomni/api_keys.sqlite` |
| Runs and tasks store | `API_TASKS_DB_PATH` / `PHYTOMNI_TASKS_DB`   | `server_tasks.db`                 |
| Per-key req/min      | `API_RATE_LIMIT_PER_MIN`                    | `120` (`<= 0` disables)           |
| Succeeded-run TTL    | `API_RUN_TTL_OK_HOURS`                      | `24`                              |
| Failed-run TTL       | `API_RUN_TTL_FAIL_DAYS`                     | `7`                               |

The runs table and tasks table share one SQLite file so the submit-side
writer and the run-status reader address the same source of truth.

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
{"error": {"type": "...", "code": "...", "message": "...", "request_id": "..."}}
```

Over-budget callers get `429` with `Retry-After`. SSE streaming is
supported only on streaming-capable chat models — `phyto-chat` in
v1; every other chat-like model with `stream: true` returns `400`
with a per-model message (`streaming is not supported for model phyto-knowledge`, etc.). See the SSE Streaming section below.

## Endpoints

| Method   | Path                           | Auth | Purpose                                                                                                  |
| -------- | ------------------------------ | ---- | -------------------------------------------------------------------------------------------------------- |
| `GET`    | `/healthz`                     | no   | Liveness, no dependencies.                                                                               |
| `GET`    | `/readyz`                      | no   | Readiness, checks local store directories without creating files.                                        |
| `GET`    | `/v1/models`                   | yes  | Lists OpenAI-compatible model ids.                                                                       |
| `POST`   | `/v1/chat/completions`         | yes  | OpenAI-compatible chat endpoint.                                                                         |
| `GET`    | `/v1/agents`                   | yes  | Lists native agent-run slugs; each row carries `legacy_aliases`.                                         |
| `POST`   | `/v1/agents/{agent}/runs`      | yes  | Invokes one agent by slug.                                                                               |
| `GET`    | `/v1/runs/{run_id}`            | yes  | Returns one owner-isolated run state.                                                                    |
| `GET`    | `/v1/runs/{run_id}/logs`       | yes  | Returns reconciled task logs for a run.                                                                  |
| `GET`    | `/v1/runs`                     | yes  | Lists owner-scoped runs newest-first.                                                                    |
| `POST`   | `/v1/files`                    | yes  | Stores one multipart upload in OBS and returns the public path.                                          |
| `POST`   | `/v1/api-keys`                 | svc  | Mints a per-user `ptm_...` API key.                                                                      |
| `GET`    | `/v1/api-keys`                 | svc  | Lists per-user keys (metadata only); optional `?user_id=` filter.                                        |
| `DELETE` | `/v1/api-keys/{prefix}`        | svc  | Revokes the key with the given public prefix.                                                            |
| `GET`    | `/v1/relay/audit`              | svc  | Lists relay audit records (service token); filters by user, key prefix, service, status, and time range. |
| `GET`    | `/v1/relay/audit/{request_id}` | svc  | Fetches relay audit records by request id (service token).                                               |
| `GET`    | `/v1/relay/healthz`            | yes  | Liveness probe for the relay; returns `{"status": "ok"}` when relay is enabled.                          |

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

`GET /v1/runs?user_id=<other-user>` lets an upstream operator list
any tenant's runs. The user key in `Authorization: Bearer ptm_...`
still authenticates the caller for rate-limit + audit, but the
`user_id` parameter is honoured only when a valid service token
arrives in `X-Service-Token: <token>` (the soft-check prefers the
dedicated header so a Bearer-carried user key cannot get confused
for the service token). Missing or wrong service token returns
`403 user_id query parameter requires the service token`. The
owner-only path (no `user_id`) keeps its existing contract.

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

`POST /v1/files` accepts one `multipart/form-data` upload through the
standard `file` field and an optional `purpose` field. The `purpose`
value MUST be one of the OpenAI-files compatible literals
`agent_context` (default) / `assistants` / `batch` / `fine-tune` /
`vision` / `user_data`; any other value returns `422` through the
unified error envelope. The response carries the OpenAI-files
compatible shape — `id` / `object: "file"` / `bytes` / `filename` /
`purpose` / `created_at` — plus `obs_path` (the public
`/obs/<bucket>/<key>` path) and a `path` alias on `obs_path` so
clients can replay it in any later `obs_file_list` argument without
translation.

Filename sanitization is a deliberate **sanitize-and-accept** policy
(not a 400 rejection): `Path.name` collapses any path-traversal
segments to the basename and `safe_path_segment` rewrites shell
metacharacters and Unicode into `-`, preserving the suffix.
Examples:

- `../../etc/passwd` → stored as `passwd`, response returns `201`.
- `my report (final).pdf` → stored as `my-report-final.pdf`, `201`.

Only empty bodies, empty filenames, and `.` / `..` filenames return
`400` through the unified error envelope. The OBS object key follows
`agent_data/uploads/{user_id}/{request_id}/{file_id}/{safe_filename}`
so uploads are isolated per authenticated principal and traceable
back to the originating HTTP call through the `X-Request-Id`
response header.

Size ceiling is `API_UPLOAD_MAX_BYTES` (25 MiB default). The route
defends in two layers: a `Content-Length` pre-check rejects honest
oversize requests before reading the body, and a chunked reader
(`read_with_byte_budget`, 64 KiB chunks) caps cumulative reads when
`Content-Length` is absent or falsified (`Transfer-Encoding: chunked`),
aborting at the first chunk that pushes past the limit so peak
memory stays bounded. Both paths return `413`. Example:

```bash
curl -s http://127.0.0.1:8080/v1/files \
  -H "Authorization: Bearer ptm_..." \
  -F file=@report.pdf \
  -F purpose=agent_context
```

`POST /v1/chat/completions` and `POST /v1/agents/{agent}/runs` accept
an optional `dialogue_id` field that groups runs into one visible
thread on the chat-ai history page. The Bot persists it onto the
`runs` row alongside `query` / `tool_name` / `model`; a missing
`dialogue_id` stays NULL. `dialogue_id` is opaque to the Bot — Web
Go assigns it.

Routes marked **svc** require the service token configured via
`API_SERVICE_TOKEN`, sent as `Authorization: Bearer <token>` or
`X-Service-Token: <token>`. When the env var is unset the routes return
`503 admin path not enabled`; an absent or wrong token returns `401`.
The service token is intentionally separate from `ptm_...` user keys
so a leaked user key cannot escalate to key-issuance scope.

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
`formatted.references`. The `raw.phytomni_state` namespace carries the
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
`text/event-stream` carrying one
`data: {chat.completion.chunk JSON}\n\n` line per provider chunk and
a terminating `data: [DONE]\n\n` so the client closes its
`EventSource` on the first match instead of waiting for the read
timeout.

v1 wires streaming only on `phyto-chat`. Every other chat-like model
(`phyto-knowledge`, `phyto-review`, `phyto-brief-gene`) returns `400`
with `streaming is not supported for model <name>` so clients see a
clear per-model signal instead of a silent fallback. The
streaming-capable set is maintained in
`src/mcp_server_phytomni/api/openai_mapping.py:_STREAM_CAPABLE_TOOLS`.

`resolve_gene_id=true` + `stream=true` is unreachable by
construction: `resolve_gene_id` is BriefGene-only (the resolver
preprocessor rejects other models with `400`) and BriefGene is not
streaming-capable. The combination therefore always `400`s — against
`phyto-chat` via the BriefGene-only gate, against `phyto-brief-gene`
via the streaming-capable gate.

```bash
curl -N -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-chat","stream":true,"messages":[{"role":"user","content":"Explain C3 photosynthesis."}]}'
```

Auth, rate-limit, request-id, OBS-file processing, and message
flattening all complete *before* the stream starts, so a
`stream=true` request that fails any precondition surfaces as a
normal JSON error envelope (`401` / `429` / `400`) instead of an
empty `text/event-stream`. After the stream drains, the run-record
is written once with `result = {"formatted": {"answer": "[streamed]"}, "raw": null, "stream": true, "completed": bool}`;
the `completed` flag distinguishes a normal drain from a client
disconnect or mid-stream provider error so `/v1/runs` callers can
surface partial calls.

Open-stream transport failures (`ConnectError` / `TimeoutException`)
are retried once before raising; once the iterator returns, any
mid-stream failure propagates immediately (a silent retry would
re-emit chunks the client already received and corrupt the SSE
timeline). See `agents/chat/service.py:MAX_OPEN_STREAM_RETRIES`.

### BriefGene `resolve_gene_id`

`POST /v1/chat/completions` and `POST /v1/agents/brief_gene/runs` accept an
optional boolean `resolve_gene_id`. Default is `false`. When `true` the
HTTP layer issues one structured LLM call (json_schema response format)
to resolve the free-form user message into a single canonical
gene/transcript identifier before invoking `BriefGeneAgent`. Use it when
external clients submit symbols, species names, or full research
questions rather than the bare locus id BriefGene expects.

The flag is BriefGene-only. The chat path requires `model="phyto-brief-gene"`;
the native path requires the `brief_gene` slug. Passing
`resolve_gene_id=true` to any other model or slug returns `400`. A
resolver failure (blank input, empty candidates, non-JSON LLM output,
timeout) also returns `400` carrying the resolver reason in
`error.message`; failed resolutions are never silently downgraded to a
nogeneid call.

When resolution succeeds the response `metadata` includes
`original_query`, `resolved_gene_id`, and `resolve_gene_id: true` so
clients can verify which canonical id BriefGene actually saw. The LLM
call rides on the shared `~90d` `phyto_chat` cache, so repeated identical
queries reuse the prior resolution at zero additional model cost.

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-brief-gene","resolve_gene_id":true,"messages":[{"role":"user","content":"What does AT5G42800 do in Arabidopsis?"}]}'

curl -s http://127.0.0.1:8080/v1/agents/brief_gene/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"rice TPR6 function","resolve_gene_id":true}}'
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

### Per-Agent `formatted.metadata` Keys

Default-mode responses include a curated subset of LangGraph
intermediate state in `formatted.metadata` so clients can read the
actually-executed query, plan, or goal list without toggling
`debug=true`. Full intermediate state remains in
`raw.phytomni_state` under debug mode; metadata is a curated subset.

| Agent                                         | Default-mode `formatted.metadata` keys                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------ |
| DataAgent                                     | `user_query`, `rewrite_query`, `is_rewrite`                                                |
| KnowledgeAgent / ReviewAgent / BriefGeneAgent | (cited; no extra metadata beyond stability note)                                           |
| AnalystAgent                                  | `plan` (≤4 KB), `extracted_tools`, `method_context_keys`, `plan_retries`, plus task fields |
| DeepGenomeAgent                               | `task_id`, `output_dir`, `species_code`, `gene_id`, `compute_resource`, plus task fields   |
| InSilicoResearchAgent                         | `task_ids`, `goals`, `output_dir`, `error`, plus task fields                               |
| DigitalDesignAgent                            | `task_ids`, `goal_description` (≤256 B), plus task fields and `output_dirs` field          |
| GeneNetworkAgent                              | `goal_description` (≤256 B), plus task fields                                              |

Text fields exceeding their byte cap are truncated with a marker
pointing to the full document in `raw.phytomni_state.<key>`.

Remote agents (`analyst`, `deep_genome`, `research`, `design`, `network`)
respond `202` with `status: "running"` and `task_ids` listing every child
task registered by the submit path. Poll `/v1/runs/{run_id}` for live
status.

### Remote agent edge cases: `id: null` / `task_ids: []`

A `202` body can legitimately return `id: null` with `task_ids: []` for
two reasons. Both produce the same identity-empty shape, so clients
distinguish them through the extra signals described here:

| Cause                       | `id`   | `task_ids` | Body extras                                     | `result` extras                                                 | Client follow-up                                                                                                |
| --------------------------- | ------ | ---------- | ----------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Healthy submission          | `str`  | `[…]`      | —                                               | per-agent payload                                               | Poll `/v1/runs/{id}` for status.                                                                                |
| Analyst dedup-hit           | `null` | `[]`       | —                                               | `result.task_id` (prior caller's id) + `result.dedup_hit: true` | Poll the prior task id directly (it is reachable through `/v1/runs` listings or the prior caller's run id).     |
| Local registry write failed | `null` | `[]`       | `degraded_tracking: true` at the body top level | per-agent payload (the remote submission did succeed)           | Treat the remote run as in-flight but not locally tracked; operators should reconcile from the upstream system. |

The `degraded_tracking: true` body field is added only when the local
SQLite chokepoint (`runtime.submit_recorder.record_submitted_task`) hit
a `sqlite3.Error` / `OSError` while writing the `runs` and `tasks`
rows after the remote platform already accepted the submission. The
remote task is alive upstream, but `GET /v1/runs/{run_id}` will return
`404` until the registry write succeeds (a manual reconcile from the
upstream platform is the recovery path). The chokepoint also writes
the full traceback through `logger.exception` so operators see the
underlying SQLite or OS error in logs.

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
`index`), `usage` (3 token fields), `formatted` (without `answer`).

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

## Analyst Dedup-hit Passthrough

`POST /v1/agents/analyst/runs` may return `202` with `id=null` and
`task_ids=[]` when the submission fingerprint matches a prior in-flight or
succeeded task. The submit path skips a fresh registry write so the prior
caller's `run_id` stays authoritative.

The prior `task_id` is still surfaced under `result["task_id"]` together
with `result["dedup_hit"]: true`. Clients should poll the prior task through
that id instead of `/v1/runs/{run_id}`.

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
