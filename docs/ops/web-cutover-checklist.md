# Web Service Cutover Checklist

This checklist tracks the Bot-side delivery against the Web cutover
handoff acceptance items. Each row records implementation evidence
(commits, docs, curl) and the candidate-A consumer model under which
Bot is now operated. Endpoint contracts live in
[HTTP API](../http-api.md); operator procedures live in
[HTTP API Operations Runbook](http-api-runbook.md); decision rationale
lives in `.codex/integration/web-consolidation-decisions.md`.

> Note: the `.codex/integration/*` paths cited as "Evidence" below are
> local-only working ADRs — they are gitignored and do NOT ship in the
> tracked repository tree, so they are unresolvable from a fresh clone
> or the GitHub UI. The authoritative tracked references are
> [HTTP API](../http-api.md) for every endpoint contract and the
> runtime `/v1/agents.legacy_aliases` field for the tool-name mapping.

## Scope

Audience: Phytomni-Bot operators preparing for cutover; Phytomni-Web
maintainers performing sign-off; release coordination.

In scope: cutover-day verification of every Bot HTTP endpoint the
Web Go service will call.

Out of scope: per-endpoint operations (see runbook); MCP stdio
surface (unchanged by this cutover); Web-side migration of
`s_question_agent_logs` (deferred until joint schema-slimming work).

## Consumer Model (candidate A)

Bot is operated as a single-tenant backend behind Web Go:

- Web Go holds one shared `ptm_<web>` user key, minted via the
  service token and rotated by Web ops on a 90-day cadence.
- Bot sees `user_id="web"` for every authenticated call; real-user
  isolation is enforced by Web Go with `WHERE real_user_id=?`
  filters against its own MySQL tables (`s_dialogue_owner`,
  `s_user_turn_reactions`).
- chat-ai never reaches Bot directly. Bot is internal-only.
- `dialogue_id` (string) and `bot_run_id` (string) are the shared
  identifiers Web Go uses to JOIN Bot data with its own user tables.

The candidate-B path (chat-ai direct to Bot + per-user keys) is
explicitly out of scope for this cutover.

## Handoff Acceptance Items

### 1. `/v1/api-keys` issue endpoint

- Status: delivered

- Evidence:

  - Config gate: `9ad3fbd` (`PHYTOMNI_API_SERVICE_TOKEN`)
  - Service-principal dependency: `e09cc8f`
  - Routes (POST / GET / DELETE): `2ab7c17`
  - Docs: `c14f616` + runbook mirror `1d721c7`

- Curl (Web ops 90-day rotation):

  ```bash
  curl -X POST http://bot.internal:8080/v1/api-keys \
    -H "Authorization: Bearer $PHYTOMNI_API_SERVICE_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"web","name":"web-go-2026-q3"}'
  ```

- Notes:

  - Candidate A: production caller is Web ops, not chat-ai login.
  - Service token is OFF-BY-DEFAULT — unset = `/v1/api-keys/*` → 503.

### 2. `/v1/chat/completions` SSE streaming

- Status: delivered (`phyto-chat` only)

- Evidence: `6119d22` (route + `_STREAM_CAPABLE_TOOLS = {"ChatAgent"}`)

- Curl:

  ```bash
  curl -N -X POST http://bot.internal:8080/v1/chat/completions \
    -H "Authorization: Bearer $PTM_WEB_KEY" \
    -d '{"model":"phyto-chat","messages":[{"role":"user","content":"hi"}],"stream":true}'
  ```

- Support matrix:

  | Model              | `stream=true` | `stream=false` |
  | ------------------ | ------------- | -------------- |
  | `phyto-chat`       | 200 SSE       | 200 JSON       |
  | `phyto-knowledge`  | 400           | 200 JSON       |
  | `phyto-review`     | 400           | 200 JSON       |
  | `phyto-brief-gene` | 400           | 200 JSON       |
  | unknown            | 404           | 404            |

- Notes: SSE frames are `data: {...}\n\n` chunks terminated by
  `data: [DONE]\n\n`. `resolve_gene_id=true` + `stream=true` → 400.

### 3. `/v1/agents` covers Web tool aliases

- Status: delivered (alias as metadata only)

- Evidence: `.codex/integration/tool-name-mapping.md` §1
  (authoritative mapping); `/v1/agents.legacy_aliases` exposes the
  same mapping at runtime.

- Curl:

  ```bash
  curl http://bot.internal:8080/v1/agents \
    -H "Authorization: Bearer $PTM_WEB_KEY"
  ```

- Notes: route slug accepts only canonical names (`chat`,
  `knowledge`, `data`, `review`, `analyst`, `deep_genome`,
  `research`). Web alias → slug resolution is chat-ai's
  responsibility per the OQ-4 decision; Bot will not silently
  translate aliases.

### 4. `/v1/agents/{agent}/runs` long-task polling

- Status: delivered (pre-existing)

- Evidence: `api/app.py:_invoke_agent_run`; reuses
  `RunRegistry.reconcile(run_id, owner)` for non-blocking status.

- Curl:

  ```bash
  curl -X POST http://bot.internal:8080/v1/agents/analyst/runs \
    -H "Authorization: Bearer $PTM_WEB_KEY" \
    -d '{"goal_description":"...","data_list":{},"obs_file_list":[]}'
  # → {"id":"run_...","status":"running","task_ids":["task_..."]}

  curl http://bot.internal:8080/v1/runs/run_... \
    -H "Authorization: Bearer $PTM_WEB_KEY"
  ```

### 5. `/v1/runs?dialogue_id=` history query

- Status: delivered

- Evidence: `74c1b9a` (run-request 5 columns) + `c50f28f` (date
  filter) + `72ea89d` (chat + agent persistence) + `1b3bb8e`
  (delegated `user_id` + time range)

- Curl:

  ```bash
  # Web Go self-query (single key, ?user_id= not needed)
  curl "http://bot.internal:8080/v1/runs?dialogue_id=d-1" \
    -H "Authorization: Bearer $PTM_WEB_KEY"

  # Service-token delegated lookup (ops debug). Needs BOTH a valid
  # user key (passes the per-request principal check) AND the service
  # token in X-Service-Token (authorizes the cross-user ?user_id=).
  curl "http://bot.internal:8080/v1/runs?user_id=web&created_after=2026-05-01T00:00:00Z" \
    -H "Authorization: Bearer $PTM_WEB_KEY" \
    -H "X-Service-Token: $PHYTOMNI_API_SERVICE_TOKEN"
  ```

- Response row fields: `id`, `user_id`, `agent`, `status`,
  `created_at`, `updated_at`, plus `dialogue_id`, `query`,
  `tool_name`, `model`, `answer` (lifted from
  `result.formatted.answer`).

### 6. `/v1/runs/{run_id}/logs`

- Status: delivered

- Evidence: `7cf4831` (`task_log` column) + `962fe8e` (get/set
  helpers) + `f3d5351` (cache-first reconcile bridge) + `2b32581`
  (route) + `bb33b69` (docs)

- Curl:

  ```bash
  curl http://bot.internal:8080/v1/runs/run_.../logs \
    -H "Authorization: Bearer $PTM_WEB_KEY"
  # → {"run_id":"run_...","task_ids":[...],"task_logs":[...]}
  ```

- Contract: the response is locked to exactly three keys —
  `run_id`, `task_ids`, and `task_logs`. The contract does **not**
  include `object`, `init_info`, `steps`, or a `tasks` top-level
  lift; each reconciled per-task log lives inside the `task_logs`
  array verbatim. Under the candidate-A architecture every log
  belongs to the single `web` user, so the route does not accept a
  delegated `?user_id=` query — a future multi-Web SaaS rollout
  would revisit that symmetry at the same time as the candidate-B
  owner-key model is reconsidered.

- Notes: `?debug=true` unstrips remote `live_status` debug fields.
  Local cache (`tasks.task_log` column) short-circuits the remote
  fetch on a hit; remote failure falls back to cached value (may be
  None) and does not raise.

### 7. File ingestion: Bot-owned `/v1/files`

- Status: delivered

- Evidence: `3c277e1` (python-multipart dep) + `8030da0` (storage
  helper) + `e7ae05d` (POST /v1/files route) + `1ee0d9e`
  (path-traversal sanitize + `purpose` Literal enum) + `d7d6b5f`
  (docs sync)

- OBS key shape:
  `agent_data/uploads/{user_id}/{request_id}/{file_id}/{safe_filename}`

- Curl:

  ```bash
  curl -X POST http://bot.internal:8080/v1/files \
    -H "Authorization: Bearer $PTM_WEB_KEY" \
    -F "file=@/tmp/test.txt" \
    -F "purpose=agent_context"
  ```

- Notes:

  - 25 MiB cap (`API_UPLOAD_MAX_BYTES`).
  - Double-layer 413 defense (Content-Length pre-check +
    chunked-read budget abort) for both well-formed and
    `Transfer-Encoding: chunked` requests.
  - Path traversal → sanitize-to-basename, 201 (not 400).
  - `purpose` ∈ Literal\[`agent_context`, `assistants`, `batch`,
    `fine-tune`, `vision`, `user_data`\].

### 8. Persistence selected + documented

- Status: delivered (SQLite)
- Evidence: `.codex/integration/web-consolidation-decisions.md` §1
- Schema:
  - `api_keys.sqlite` — key hashes; default
    `.cache/phytomni/api_keys.sqlite`
  - `server_tasks.db` — `runs` (15 columns incl. `dialogue_id`,
    `query`, `tool_name`, `model`, `request_json`) + `tasks` (incl.
    `task_log` text column for log reconciliation)
- Notes: NFS / network-FS incompatible (SQLite WAL deadlock). Pin
  DB paths to local filesystem.

### 9. Tool name mapping table

- Status: delivered (alias as metadata only)
- Evidence: `.codex/integration/tool-name-mapping.md` §1
- Notes: Bot route slug accepts ONLY canonical names; Web alias →
  slug resolution is chat-ai's responsibility (OQ-4 decision).

### 10. Error response contract

- Status: delivered
- Evidence: `api/schemas.py:ApiErrorResponse` + `docs/http-api.md`
  §Errors
- Contract: HTTP error envelope is FastAPI's `{"detail": "..."}`
  for HTTPException-derived errors. 4xx domain errors carry a
  textual reason; 5xx include `X-Request-Id` for log correlation.
  Web Go is responsible for translating `{"detail": ...}` to its
  own `{"code": ..., "message": ...}` shape if needed downstream.

### 11. Auth header + request headers

- Status: delivered
- Format: `Authorization: Bearer ptm_<user-key>` (per-user keys
  use the `ptm_` prefix; service token is a free-form opaque
  string).
- Optional headers:
  - `X-Service-Token` — alternative service-principal credential
    location (precedence: header > `Authorization: Bearer`)
  - `X-Request-Id` — operator-supplied request correlation ID
    (mirrored into response and access log)
- Notes: candidate A does not require Web to send `X-User-Id` or
  `X-Tenant-Id`; per-user isolation is enforced upstream in Web Go.

### 12. Deployment endpoint URL

- Status: delivered to Web team out-of-band
- Notes: per-environment URLs (prod / staging / dev) are
  distributed through the ops secure-config channel and never
  committed. Production topology: Bot is internal-only (not
  internet-facing); Web Go is the only external gateway.

## Deferred Items (post-cutover)

| Item                                  | Owner  | Trigger                     |
| ------------------------------------- | ------ | --------------------------- |
| ETL `s_question_agent_logs` → `runs`  | joint  | Web Phase X3 schema slim    |
| Per-real-user rate limiting           | Web Go | when load thresholds emerge |
| Multi-key Bot client (purpose-routed) | both   | first multi-bucket need     |

## Sign-Off Procedure

Web-team T3 sign-off requires confirmation that:

1. Web Go has provisioned its `ptm_<web>` key via the service
   token (item 1).
1. Web Go can call every item-2..7 endpoint with the expected
   shape under candidate A.
1. Web Go has scheduled the 90-day key rotation cron.
1. Web Go has documented its `WHERE real_user_id=?` filter
   coverage for all routes that JOIN Bot runs.
1. chat-ai's vite proxy targets Web Go (`/query` → :8082), NOT
   Bot.

Bot-team sign-off is "all 12 items delivered + rollback verified

- live e2e suite green" (per `e2e/test_api_http_e2e.py`, run with
  `PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1`).

## Rollback

Endpoint-level rollback is `git revert` of the implementing
commit range. Bot endpoints are additive; reverting one does not
change unrelated endpoints. For per-endpoint operational rollback
(e.g. disabling `/v1/files` while keeping chat), see
[HTTP API Operations Runbook](http-api-runbook.md) §Triage.
