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
| Checkpoint store     | beside the runs/tasks store                 | `checkpoints.db`                  |
| Per-key req/min      | `API_RATE_LIMIT_PER_MIN`                    | `120` (`<= 0` disables)           |
| Succeeded-run TTL    | `API_RUN_TTL_OK_HOURS`                      | `24`                              |
| Failed-run TTL       | `API_RUN_TTL_FAIL_DAYS`                     | `7`                               |

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
{"error": {"type": "...", "code": 400, "message": "...", "request_id": "..."}}
```

Over-budget callers get `429` with `Retry-After`. SSE streaming is
supported only on streaming-capable chat models — `phyto-chat`,
`phyto-knowledge`, and `phyto-brief-gene`; `phyto-review` human-in-
the-loop runs must use non-stream chat or native runs plus
`/v1/runs/{id}/resume`. Every other chat-like model with
`stream: true` returns `400` with a per-model message. See the SSE
Streaming section below.

## Endpoints

| Method   | Path                                     | Auth  | Purpose                                                                                                                                                                                                 |
| -------- | ---------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/healthz`                               | no    | Liveness, no dependencies.                                                                                                                                                                              |
| `GET`    | `/readyz`                                | no    | Readiness, checks local store directories without creating files.                                                                                                                                       |
| `GET`    | `/v1/models`                             | yes   | Lists OpenAI-compatible model ids.                                                                                                                                                                      |
| `POST`   | `/v1/chat/completions`                   | yes   | OpenAI-compatible chat endpoint.                                                                                                                                                                        |
| `GET`    | `/v1/agents`                             | yes   | Lists native agent-run slugs; each row carries `legacy_aliases`.                                                                                                                                        |
| `POST`   | `/v1/agents/{agent}/runs`                | yes   | Invokes one agent by slug.                                                                                                                                                                              |
| `POST`   | `/v1/query/route`                        | yes   | Autonomous Expert routing: an LLM selects the agent for a query and returns its `agent.run` envelope with the resolved slug.                                                                            |
| `GET`    | `/v1/runs/{run_id}`                      | yes   | Returns one owner-isolated run state.                                                                                                                                                                   |
| `POST`   | `/v1/runs/{thread_id}/resume`            | yes   | Resumes a ReviewAgent run paused at a human approval interrupt.                                                                                                                                         |
| `POST`   | `/v1/runs/{run_id}/a2ui-actions`         | yes   | Resumes a ChatAgent or ReviewAgent run paused on an A2UI confirm surface (`input_required`).                                                                                                            |
| `GET`    | `/v1/runs/{run_id}/logs`                 | yes   | Returns reconciled task logs for a run.                                                                                                                                                                 |
| `GET`    | `/v1/runs`                               | yes   | Lists owner-scoped runs newest-first.                                                                                                                                                                   |
| `POST`   | `/v1/files`                              | yes   | Stores one multipart upload in OBS and returns the public path.                                                                                                                                         |
| `POST`   | `/v1/api-keys`                           | svc   | Mints a per-user `ptm_...` API key.                                                                                                                                                                     |
| `GET`    | `/v1/api-keys`                           | svc   | Lists per-user keys (metadata only); optional `?user_id=` filter.                                                                                                                                       |
| `DELETE` | `/v1/api-keys/{prefix}`                  | svc   | Revokes the key with the given public prefix.                                                                                                                                                           |
| `GET`    | `/v1/relay/audit`                        | svc   | Lists relay audit records (service token); filters by user, key prefix, service, status, and time range.                                                                                                |
| `GET`    | `/v1/relay/audit/{request_id}`           | svc   | Fetches relay audit records by request id (service token).                                                                                                                                              |
| `GET`    | `/v1/relay/healthz`                      | no    | Liveness probe for the relay; no auth, only the relay-enabled guard; returns `{"status": "ok"}` when relay is enabled and `404` when relay is disabled.                                                 |
| `POST`   | `/v1/relay/llm/chat/completions`         | relay | Chat LLM relay (transparent); injects the operator `Authorization: Bearer` key.                                                                                                                         |
| `POST`   | `/v1/relay/coder/chat/completions`       | relay | Coder model relay (transparent); injects the operator coder Bearer key.                                                                                                                                 |
| `POST`   | `/v1/relay/embed/embeddings`             | relay | Embedding relay (transparent); injects the operator embed Bearer key (OpenAI shape, OQ-001).                                                                                                            |
| `POST`   | `/v1/relay/retrieve/search`              | relay | Knowledge retrieve relay (envelope); no operator credential injected.                                                                                                                                   |
| `POST`   | `/v1/relay/rerank/rank`                  | relay | Knowledge rerank relay (envelope); no operator credential injected.                                                                                                                                     |
| `POST`   | `/v1/relay/database/nl2sql`              | relay | NL2SQL relay (envelope); injects the operator IAM `X-Auth-Token`.                                                                                                                                       |
| `POST`   | `/v1/relay/bi/query`                     | relay | BI relay (envelope); server-side-terminated — the operator runs `gauss_query` against GaussDB, no credential forwarded.                                                                                 |
| `GET`    | `/v1/relay/obs/object`                   | relay | OBS object download relay; streams a tenant-namespace-confined object (key re-validated to `agent_data/{user_data,uploads}/<key user id>/`) under a response-size budget, via operator OBS credentials. |
| `GET`    | `/v1/relay/obs/list`                     | relay | OBS object list relay; enumerates keys under the caller tenant's output root (`agent_data/user_data/<key user id>/`) via operator OBS credentials.                                                      |
| `PUT`    | `/v1/relay/obs/object`                   | relay | OBS object upload relay; writes the request body at a tenant-namespace-confined key via operator OBS credentials.                                                                                       |
| `PUT`    | `/v1/relay/obs/dir`                      | relay | OBS dir-marker relay; creates a zero-byte directory marker at a tenant-namespace-confined key via operator OBS credentials.                                                                             |
| `POST`   | `/v1/relay/analysis/tasks`               | relay | Analysis-platform submit relay (envelope); injects the operator IAM `X-Auth-Token` for the analysis region.                                                                                             |
| `GET`    | `/v1/relay/analysis/{task_id}`           | relay | Analysis task-status relay (envelope); validates the task id and injects the operator IAM `X-Auth-Token`.                                                                                               |
| `GET`    | `/v1/relay/analysis/{task_id}/logs`      | relay | Analysis task-log relay (envelope); injects IAM `X-Auth-Token` and forwards only the `task_name` query key.                                                                                             |
| `POST`   | `/v1/relay/analysis/{task_id}/terminate` | relay | Analysis task-terminate relay (envelope); validates the task id and injects the operator IAM `X-Auth-Token`.                                                                                            |
| `GET`    | `/v1/relay/spa-faq/{repo_id}`            | relay | SPA-FAQ relay (envelope); validates the repo id, injects the operator IAM `X-Auth-Token`, forwards only `question`/`page_size`/`page_num`, and bypasses the host proxy.                                 |

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

`POST /v1/runs/{thread_id}/resume` accepts
`{"approved": bool, "edits": string | null}` for a ReviewAgent run
whose current status is `input_required`. The `thread_id` is the same
value as the Bot `run_id` returned in the interrupt body. Unknown runs
return `404`, terminal or otherwise non-paused runs return `409`,
malformed resume bodies return FastAPI's normal `422`, and a missing
checkpoint returns `409 no pause point for run`. If the resumed graph
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

Copyable A2UI downlink / uplink / success / error goldens live under
[`docs/contracts/a2ui/`](../contracts/a2ui/README.md) for Web and Go
gateway consumers: `chat_confirm`, `review_confirm`, `chat_form`,
`chat_choice`, `review_form`, `review_choice`, plus
`multi_turn/round2_downlink.json` (`sfc-contract-2`). Those fixtures
lock shapes only; this section and the offline HTTP tests remain
authoritative for runtime behavior.

| Condition                    | HTTP  | Detail                             |
| ---------------------------- | ----- | ---------------------------------- |
| `A2UI_ENABLED` off           | `403` | `a2ui disabled`                    |
| Unknown or foreign run       | `404` | `run not found: <run_id>`          |
| Path/body `run_id` mismatch  | `400` | `run_id mismatch`                  |
| Invalid widget payload       | `400` | e.g. missing `accepted` on confirm |
| Run not `input_required`     | `409` | `run is not awaiting input`        |
| No open surface on run       | `409` | `no open a2ui surface`             |
| `surface_id` ≠ draft         | `409` | `surface_id mismatch`              |
| Missing LangGraph checkpoint | `409` | `no pause point for run`           |
| Second POST after success    | `409` | terminal run                       |
| Malformed body               | `422` | FastAPI validation                 |

On success the run settles `succeeded` and the response carries the
normal `agent.run` envelope with `result.formatted.answer` (the real
agent answer, or the short cancel string on Chat reject / form or
choice cancel) plus `result.a2ui` when the pause carried a projected
surface: the prior downlink cloned with `props.status: "submitted"`
and `props.accepted`, `props.cancelled`, `props.fields`, or
`props.selected` when applicable. Both `/resume` and `/a2ui-actions` attach `result.a2ui`
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
audit write never fails a successful relay); audit rows store the
verbatim request/response bodies (capped for the response by
`RELAY_RESPONSE_AUDIT_MAX_BYTES`) and the public key prefix, never the
key hash or any injected credential header. Query audits with the
service-token `GET /v1/relay/audit` routes.

See *Relay Variables* in `docs/reference/configuration.md` for the knobs and the
*Relay* section of `docs/ops/http-api-runbook.md` for operator
procedures.

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
`text/event-stream`. For `phyto-chat` each frame carries one
`data: {chat.completion.chunk JSON}\n\n` line per provider chunk; for
`phyto-knowledge` / `phyto-brief-gene` the stream carries AG-UI event
frames (`event: RunStarted`, one `event: StepStarted` per graph stage,
`event: Custom` frames for `phyto.progress` / `phyto.references` /
`phyto.follow_up`, a one-shot `event: TextMessageContent` answer, then
`event: RunFinished`). Both shapes end with a terminating
`data: [DONE]\n\n` so the client closes its `EventSource` on the first
match instead of waiting for the read timeout.

Streaming is wired on `phyto-chat`, `phyto-knowledge`, and
`phyto-brief-gene`: ChatAgent token-streams provider deltas, while
KnowledgeAgent / BriefGeneAgent drive their compiled graphs through
the `_stream_graph_agent` primitive (stage `StepStarted` frames then a
terminal answer + citations). `phyto-review` with `stream: true` returns
`400` when `A2UI_ENABLED` is off because human-in-the-loop review
pauses resume through the non-stream flow plus `/resume`. When
`A2UI_ENABLED` is on, `phyto-review` with `stream: true` emits a
minimal pause stream: `RunStarted` → one `phyto.a2ui` confirm frame →
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
  -d '{"model":"phyto-chat","stream":true,"messages":[{"role":"user","content":"Explain C3 photosynthesis."}]}'
```

Auth, rate-limit, request-id, OBS-file processing, and message
flattening all complete *before* the stream starts, so a
`stream=true` request that fails any precondition surfaces as a
normal JSON error envelope (`401` / `429` / `400`) instead of an
empty `text/event-stream`. After the stream drains, the run record is settled from the wrapper
`finally` block:

- **ChatAgent (`phyto-chat`)**: `result` is
  `{"formatted": {"answer": "<accumulated text>"}, "raw": null, "stream": true, "truncated": bool, "partial": bool}`.
  `answer` is the concatenation of `TextMessageContent` deltas,
  soft-capped by `STREAM_ANSWER_MAX_BYTES` / `PHYTOMNI_STREAM_ANSWER_MAX_BYTES`
  (default 1 MiB). The SSE wire stream is never truncated.
  `truncated` is true when the stored blob hit the cap; `partial` is
  true when the run settled `failed` (client disconnect before
  `RunFinished`, or a mid-stream `RunError`).
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
- **Other streaming-capable models** (knowledge / brief_gene today):
  settle still uses
  `{"formatted": {"answer": "[streamed]"}, "raw": null, "stream": true}`
  until a follow-up change persists their answers the same way.

Open-stream transport failures (`ConnectError` / `TimeoutException`)
are retried once before raising; once the iterator returns, any
mid-stream failure propagates immediately (a silent retry would
re-emit chunks the client already received and corrupt the SSE
timeline). See `agents/chat/service.py:MAX_OPEN_STREAM_RETRIES`.

### Resolver flags: `resolve_gene_id` and `resolve_to_id`

The HTTP layer can resolve a free-form `user_query` into the canonical
identifier a downstream agent expects, before invoking the agent. Four
agents support pre-shaping today:

| Flag              | Eligible models / agents                                                       | Resolved fields                              | Metadata keys (on success)                                                             |
| ----------------- | ------------------------------------------------------------------------------ | -------------------------------------------- | -------------------------------------------------------------------------------------- |
| `resolve_gene_id` | `phyto-brief-gene` (chat path) / `brief_gene` / `deep_genome` / `design` slugs | canonical gene id (+ `species_code` on runs) | `original_query`, `resolved_gene_id`, `resolved_species_code`, `resolve_gene_id: true` |
| `resolve_to_id`   | `network` slug only                                                            | Trait Ontology id + `species_code`           | `original_query`, `resolved_to_id`, `resolved_species_code`, `resolve_to_id: true`     |

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
  -d '{"model":"phyto-brief-gene","resolve_gene_id":true,"messages":[{"role":"user","content":"What does AT5G42800 do in Arabidopsis?"}]}'

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

### Per-Agent `formatted.metadata` Keys

Default-mode responses include a curated subset of LangGraph
intermediate state in `formatted.metadata` so clients can read the
actually-executed query, plan, or goal list without toggling
`debug=true`. Full intermediate state remains in
`raw.phytomni_state` under debug mode; metadata is a curated subset.

| Agent                                         | Default-mode `formatted.metadata` keys                                                                                       |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| DataAgent                                     | `user_query`, `rewrite_query`, `is_rewrite`                                                                                  |
| KnowledgeAgent / ReviewAgent / BriefGeneAgent | (cited; no extra metadata beyond stability note; BriefGeneAgent adds `degraded` on a recovered literature fault — see below) |
| AnalystAgent                                  | `plan` (≤4 KB), `extracted_tools`, `method_context_keys`, `plan_retries`, plus task fields                                   |
| DeepGenomeAgent                               | `task_id`, `output_dir`, `species_code`, `gene_id`, `compute_resource`, plus task fields                                     |
| InSilicoResearchAgent                         | `task_ids`, `goals`, `output_dir`, `error`, plus task fields                                                                 |
| DigitalDesignAgent                            | `task_ids`, `goal_description` (≤256 B), plus task fields and `output_dirs` field                                            |
| GeneNetworkAgent                              | `goal_description` (≤256 B), plus task fields                                                                                |

Text fields exceeding their byte cap are truncated with a marker
pointing to the full document in `raw.phytomni_state.<key>`.

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

Remote agents (`analyst`, `deep_genome`, `research`, `design`, `network`)
respond `202` with `status: "running"` and `task_ids` listing every child
task registered by the submit path. Poll `/v1/runs/{run_id}` for live
status.

`deep_genome` runs the whole report workflow in-process in the
background, so unlike the other remote agents its terminal product is a
local markdown report rather than an upstream-platform artifact. The
last report node persists the assembled markdown on its task row; on a
succeeded poll the report surfaces in two places: `GetTaskStatus`
returns it as `formatted.answer` (instead of the bare
`Task <id>: <status>` status line), and `GET /v1/runs/{run_id}` lifts
the first child report to `result.final_report` at the payload top level
(also present per-child under `result.task_results[].final_report`).
Every other agent leaves `final_report` `null`.

A `deep_genome` report whose mounted sub-analysis degraded mid-run (its
brief_gene gene-profile, evolution, or digital-design step) still
settles as `succeeded` but flags the gap: a brief_gene gene-profile
failure adds a visible "Gene profile unavailable" banner to the report
markdown, and any degraded branch adds machine-readable keys —
`GetTaskStatus` exposes
`formatted.metadata.degraded` (bool) and
`formatted.metadata.degraded_reason` (a redacted string, or `null`),
while `GET /v1/runs/{run_id}` exposes `result.degraded` (true when any
child degraded; the per-task `degraded_reason` rides
`result.task_results[]`). Healthy and non-`deep_genome` rows read
`degraded: false` / `degraded_reason: null`.

For terminal remote analyst-class runs (`analyst`, `research`, `design`,
and `network`), a successful response exposes both a long-form report
and compact display text:

- `result.final_report` is the primary markdown report for Web and other
  clients. It is generated from safe text artifacts (`.md`, `.txt`,
  `.json`, `.csv`, `.tsv`, `.log`) through the terminal report
  synthesizer.
- `result.formatted.answer` is compact renderable text for chat/task
  summaries and remains available as a fallback surface.
- `result.artifacts[].paths` contains concrete `/obs/<bucket>/<key>`
  artifact paths for downloads and galleries. Binary artifacts, PDFs,
  xlsx files, and images are listed here even when they are not read
  into the first-version report prompt.
- `result.degraded` is `true` when report synthesis fell back or skipped
  material because artifact reading, summarization, or persistence
  degraded. The task-level `degraded_reason` is included in
  `result.task_results[]`.

Report synthesis degradation does not change a successfully completed
remote analysis run to `failed`; clients should render the fallback
report and expose the degraded signal.

Assembly is best-effort: an OBS listing failure logs a warning and
leaves that task's `paths` empty (the run still settles as terminal). The
single-task `GetTaskStatus` surface does not run this run-level assembly,
so its descriptors keep empty `paths`. A succeeded run with empty `paths`
is therefore an accepted terminal shape, not an error signal — a client
that needs the objects lists the task's `output_dir` directly (the same
fallback used before paths were globbed).

### Remote agent edge cases: `id: null` / `task_ids: []`

A `202` body can legitimately return `id: null` with `task_ids: []` for
two reasons. Both produce the same identity-empty shape, so clients
distinguish them through the extra signals described here:

| Cause                       | `id`   | `task_ids` | Body extras                                     | `result` extras                                       | Client follow-up                                                                                                |
| --------------------------- | ------ | ---------- | ----------------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Healthy submission          | `str`  | `[…]`      | —                                               | per-agent payload                                     | Poll `/v1/runs/{id}` for status.                                                                                |
| Local registry write failed | `null` | `[]`       | `degraded_tracking: true` at the body top level | per-agent payload (the remote submission did succeed) | Treat the remote run as in-flight but not locally tracked; operators should reconcile from the upstream system. |

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
  "forced_tool": null
}
```

- `history` is routing context only; it is never forwarded to the
  dispatched agent.
- `obs_file_list` is injected into the selected tool's arguments only when
  that tool accepts attachments (`chat` / `knowledge` / `review`); the LLM
  fills every other argument from the tool's schema.
- A sync agent returns `200` with `status="succeeded"`; a remote agent
  returns `202` with `status="running"` plus `task_ids`, exactly like the
  native runs path (poll `GET /v1/runs/{id}`). When the model selects no
  tool the query falls back to the chat agent.

Errors: `forced_tool` is accepted but unsupported in v1 (`400`);
LLM-extracted arguments that fail the agent schema return `400`; a tool
outside the agent set returns `502`; missing or insufficient scope returns
`401` / `403`.

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
  -d '{"user_query":"Find recent papers on rice drought tolerance genes."}'
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
