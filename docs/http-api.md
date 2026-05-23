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

Send the key as either header:

```text
Authorization: Bearer ptm_...
X-API-Key: ptm_...
```

Every response carries an `X-Request-Id`. Errors on native routes use:

```json
{"error": {"type": "...", "code": "...", "message": "...", "request_id": "..."}}
```

Over-budget callers get `429` with `Retry-After`. Streaming is not
supported; `stream: true` returns `400`.

## Endpoints

| Method | Path                      | Auth | Purpose                                                           |
| ------ | ------------------------- | ---- | ----------------------------------------------------------------- |
| `GET`  | `/healthz`                | no   | Liveness, no dependencies.                                        |
| `GET`  | `/readyz`                 | no   | Readiness, checks local store directories without creating files. |
| `GET`  | `/v1/models`              | yes  | Lists OpenAI-compatible model ids.                                |
| `POST` | `/v1/chat/completions`    | yes  | OpenAI-compatible chat endpoint.                                  |
| `GET`  | `/v1/agents`              | yes  | Lists native agent-run slugs.                                     |
| `POST` | `/v1/agents/{agent}/runs` | yes  | Invokes one agent by slug.                                        |
| `GET`  | `/v1/runs/{run_id}`       | yes  | Returns one owner-isolated run state.                             |
| `GET`  | `/v1/runs`                | yes  | Lists owner-scoped runs newest-first.                             |

`GET /v1/runs` accepts optional `status`, `agent`, `origin`, `limit`, and
`offset` query parameters.

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
extension keys). Cited-agent answers (`KnowledgeAgent`, `ReviewAgent`,
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

Remote agents (`analyst`, `deep_genome`, `research`, `design`, `network`)
respond `202` with `status: "running"` and `task_ids` listing every child
task registered by the submit path. Poll `/v1/runs/{run_id}` for live
status.

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
