# Changelog

All notable changes to **Phytomni-Bot** are recorded here. Versions are dated
snapshots of `main`; each entry maps to one or more commits landed in that
window. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Newest first.

> **Conventions.** "MCP surface" = the stdio tool API; "HTTP API" = the
> authenticated FastAPI service. "Behavior-preserving" = pure rename/move with
> no runtime contract change. "Relay mode" = the operator-fronted deployment
> where a child routes its backend calls through `/v1/relay/*`.

______________________________________________________________________

## [0.1.2] — 2026-07-07

Feature + hardening release on top of `0.1.1`. Cuts the BI query path over to a
direct GaussDB connection, adds an autonomous Expert routing endpoint, enriches
cited references with full bibliographic fields, drives the graph agents through
SSE streaming, and lands four backend reliability knobs. **One required operator
action at deploy** — provision the new `GAUSS_DSN` secret (see
[`docs/ops/upgrading.md`](docs/ops/upgrading.md)); everything else
is additive. Full commit range: `adaa874..HEAD`.

### Added

- **Direct GaussDB BI path** — a `GAUSS_DSN` secret + `asyncpg` dependency and a
  `gauss_query` seam with a per-event-loop connection pool replace the HTTP BI
  backend. In relay mode the `/v1/relay/bi/query` route is server-side-terminated
  (the operator runs the query locally; no credential is forwarded to the child).
- **Expert routing endpoint** — `POST /v1/query/route` runs one in-process
  OpenAI tool-calling completion over the shared agent tool specs to pick an
  agent per request, then delegates to the native run path. Falls back to chat
  when the routing model returns no choices.
- **Bibliographic citation enrichment** — a shared enricher batch-merges
  `au/ti/so/vl/bp/ep/py/di/dl/pm` fields into cited agents' reference lists at
  the dispatch seam, degrading to title-only on any backend error.
- **Graph-agent SSE streaming** — Knowledge and Review agents stream stage
  events through `astream`, then emit a terminal answer plus citation frames,
  wrapped in AG-UI event frames on the SSE shaper.
- **Chat completion `run_id`** — `/v1/chat/completions` now returns the Bot-side
  `run_id` join key and a sync-path `degraded_tracking: true` when the run
  registry write fails.
- **Analyst terminal reports** — terminal analyst-class runs synthesize an
  LLM-enhanced `final_report` markdown from capped text artifacts at the
  reconcile settle transition, with a deterministic fallback on summarizer
  failure, persisted through the task store and surfaced on the poll surfaces.
- **Four reliability knobs** — `GAUSS_COMMAND_TIMEOUT` (per-query GaussDB
  timeout), `HTTP_MAX_CONNECTIONS` / `HTTP_MAX_KEEPALIVE` (shared httpx pool
  limits), and `API_GRACEFUL_SHUTDOWN` (uvicorn drain window).
- **Prompt renderer** — include and conditional support with a byte-identical
  render baseline snapshot and a placeholder-coverage guard.

### Changed

- **BI query path routed through `gauss_query`** — the BriefGene / DeepGenome
  gene symbol/annotation lookups and the relay BI route all terminate against
  GaussDB directly instead of the retired HTTP BI backend.
- **MCP tool list sourced from shared agent definitions** — `list_tools` and the
  Expert router derive from one `AGENT_TOOL_DEFINITIONS` source so the two tool
  surfaces cannot drift.
- **Run-registry GC moved off the response path** — the three write routes defer
  the expired-run purge to a FastAPI `BackgroundTask`; the listing route keeps
  its inline purge so the body reflects post-purge state.
- **`FileUploadResponse.path` derived from `obs_path`** — a `@computed_field`
  read-only mirror structurally prevents the two fields from drifting.
- **Resolver candidate schema derived from the model** — BriefGene / GeneNetwork
  resolver confidence bounds come from the pydantic model's JSON schema.
- **Type annotations modernized to PEP 585/604** across the tree.

### Removed

- **`BI_URL` / `BI_TOKEN` config** — dead after the GaussDB cutover.
- **Dead `/nky` task-registration code** — no remaining caller.

### Fixed

- **Prompt-key drift** — eliminated placeholders that always rendered blank and
  added a guard against their return.
- **Blocking Path methods moved off the event loop** — `deep_genome` report file
  writes and other blocking `Path` calls run in a thread.
- **GaussDB pool per-query timeout** — bounds a query that would otherwise hold a
  connection indefinitely.
- **Admin response models wired to their routes** — the four dormant admin
  response models now back their routes via `response_model=`.

______________________________________________________________________

## [0.1.1] — 2026-06-27

Reliability and multi-tenant-dedup release. Completes the cross-tenant dedup
story with relay-side shared reads and caller-owned task ids, adds a live-task
registry that lets a poll tell a running `deep_genome` umbrella from a dead one,
a rerank concurrency throttle, and a secret-scrubbing sweep across logs.
Additive throughout — no required operator action. Full commit range:
`d59046f..adaa874`.

### Added

- **Caller-owned dedup reuse + relay shared reads** — a fingerprint-match caller
  is handed its own fresh task id (with the prior tenant's remote id kept in
  `source_task_id` for reconciliation), and the OBS relay grants reads of the
  content-addressed `agent_data/shared/<fingerprint>/` store only on possession
  of a full 64-hex fingerprint segment (the bare root is rejected to prevent
  enumeration).
- **In-flight umbrella live-task registry** — `runtime/live_tasks.py` lets
  `GetTaskStatus` and `GET /v1/runs/{run_id}` distinguish a live `deep_genome`
  umbrella from a dead one; a dead umbrella reconciles to `failed`.
- **`RERANK_CONCURRENCY` deployment knob** — a per-event-loop semaphore throttles
  concurrent rerank HTTP requests; `0` or negative disables throttling.

### Changed

- **Secrets scrubbed from all log output** — a redaction pass covers every
  emitted log line, including a token spliced onto a `Bearer` URL-query tail.
- **DeepGenome mount output invariant tenant-neutral** — the mount-dispatch
  output stays under the content-addressed fingerprint rather than a preset dir,
  with mount failure strings redacted before the debug envelope.

### Fixed

- **Analyst dedup-reuse chain carries `source_task_id`** — a reuse caller's
  reconciliation now probes the source task instead of its own fresh id.
- **DeepGenome run self-heals a lost terminal status write** at read time.
- **Encrypted-envelope encoding guard** locked at the package import seam.

______________________________________________________________________

## [0.1.0] — 2026-06-16 (baseline)

Baseline snapshot of `main` at `d59046f`. Establishes the full product surface:

- **MCP stdio server** exposing eleven tools — `ChatAgent`, `KnowledgeAgent`,
  `DataAgent`, `ReviewAgent`, `BriefGeneAgent`, `AnalystAgent`,
  `DeepGenomeAgent`, `InSilicoResearchAgent`, `DigitalDesignAgent`,
  `GeneNetworkAgent`, and the non-blocking `GetTaskStatus`.
- **Authenticated FastAPI HTTP service** with an OpenAI-compatible
  `/v1/chat/completions`, native `/v1/agents/{agent}/runs`, run polling /
  history / logs, per-user API keys, and multipart file upload.
- **Nine LangGraph agents** plus the shared two-block (`formatted` / `raw`)
  response envelope, intermediate-state surfacing, and cited-agent citation
  handling.
- **Customer relay mode** (`/v1/relay/*`) forwarding LLM / platform / OBS calls
  through the operator with a scrubbed audit trail.
- **Encrypted-envelope customer distribution** (AES-256-GCM `.env.encrypted` +
  runtime license key) and OBSFS-first storage with an OBS SDK fallback.

The 1268 commits before this baseline are not itemized here; see the git history
before `d59046f` for pre-baseline detail.
