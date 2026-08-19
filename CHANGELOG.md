# Changelog

All notable changes to **Phytomni-Bot** are recorded here. Versions are dated
snapshots of `main`; each entry maps to one or more commits landed in that
window. The format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).
Newest first.

> **Conventions.** "MCP surface" = the stdio tool API; "HTTP API" = the
> authenticated FastAPI service. "Behavior-preserving" = pure rename/move with
> no runtime contract change. "Relay mode" = the operator-fronted deployment
> where a child routes its backend calls through `/v1/relay/*`.

______________________________________________________________________

## [Unreleased]

Operator-visible changes on `release/0.1.4` since 0.1.3. The installed
package version remains `0.1.3` until the release bump.

### Added

- **Fingerprint job claims** — Analyst reuse records a first-class
  fingerprint generation plus per-run claims. Stopping the last live
  claim marks that EI job for terminate; earlier claims only detach.
  A cancelled or failed generation can be relaunched as `generation+1`.
- **Expert route logs** — `POST /v1/query/route` writes one
  `Expert route outcome` line that classifies selected, Chat
  fallback, decline without fallback, selection-contract, and
  provider timeout or failure. Each Pangu routing completion writes
  `Expert routing provider completed` with `duration_ms`. Public
  HTTP codes are unchanged.

### Changed

- **DeepGenome cancel** — An owner cancel no longer lets the
  background done-callback stamp `failed` before `/cancel` writes
  `cancelled`. A missing `id_table` row keeps the submitted gene id
  instead of crashing `prepare_tasks_node`.
- **Run cancel** — `POST /v1/runs/{id}/cancel` cancels every
  owner-scoped agent. After a Research child is sent, cancel detaches
  that child and terminates last-claim EI jobs instead of returning
  409\.
- **Compute tiers** — `AnalystConfig.RESOURCE` medium is now 4C/16G
  (was 8G). Submit sites resolve `small` / `medium` / `large` through
  `resolve_compute_resource`; InSilicoResearch defaults to `medium`.
- **Outbound example budgets** — `config/.env.example` sets LLM 32,
  retrieval/rerank/NL2SQL 16, and SPA FAQ 4. Other pools stay `0`
  (unlimited per process). Restart the process to apply.
- **A2UI** — Chat and Review widgets are always on. There is no
  `A2UI_ENABLED` field; a leftover `PHYTOMNI_A2UI_ENABLED=0` is ignored.
- **Review manuscript** — Review plans argument-led sections, drops
  off-domain papers, writes claim-led subsections, and assembles
  Abstract / Introduction / Conclusions. Citation delivery is unchanged.

### Removed

- **Design/Network `obs_file_list`** — `DigitalDesignAgent` and
  `GeneNetworkAgent` no longer advertise or accept that field.
  Managed attachments to those slugs return
  `422 attachment_not_supported`. Chat, Knowledge, Review, Analyst,
  and Research document channels are unchanged.

### Fixed

- **Expert structured ids** — After Expert selects DeepGenome, Design,
  or Network, a missing `gene_id` / `to_id` / `species_code` opens the
  native `resolve_gene_id` / `resolve_to_id` seam from the user turn.
  Complete routing extractions are unchanged. BriefGene and Analyst are
  not opted in. Resolver failure follows the native runs contract.
- **Expert routing skip** — A pinned `@agent` / `forced_tool`, or a
  one-tool trusted allowlist, dispatches that agent without calling the
  routing model. The model runs only when the caller did not pin a tool
  and the allowlist has two or more agents. Chat fallback on an unforced
  model decline is unchanged.
- **Expert routing auto** — That unpinned model call sends
  `tool_choice=auto` on the first request. A process restart no longer
  probes `required` and pays a 400 before retrying `auto`. The
  constrained-choice fallback remains for callers that still pass
  `required` or a named tool.
- **Context Expert decline envelope** — When `ChatAgent` is not in
  the trusted allowlist, a conversation-context model decline now
  returns the same `502 routing_contract_violation` envelope as V0
  instead of a generic `upstream_failed` HTTPException. Unforced
  declines that include `ChatAgent` still dispatch Chat (`CHAT_FALLBACK`).
- **Background cancel settlement** — Cancelling a detached Analyst /
  Network / Design / Research worker settles the umbrella as
  `cancelled`, not `failed`.
- **Stream owner stop** — Owner cancel of an in-flight stream settles
  `cancelled` and keeps the accumulated text as a draft. A plain
  disconnect still settles `failed`.
- **Analyst completion display** — After the analysis platform marks a
  task succeeded, the run publishes the scientific report before OBS
  harvest. Public-data cache still keys on the input fingerprint, but
  each new EI job writes under `shared/<fingerprint>/jobs/<job-id>/`.
  User-upload jobs stay in the caller-owned run directory and never
  reuse another tenant's files. Legacy shared dumps are not reused.
- **Analyst official report** — When the producer omitted
  `scientific_text` / `scientific_report` but left a small `text/plain`
  conclusion file (for example `line_count.txt`), that file becomes the
  official report body instead of the "no scientific text" fallback.
- **Analyst task logs** — `/v1/runs/{id}/logs` only treats a cache hit
  as complete when `logs[].content` has text. An early empty `{}` no
  longer blocks a later platform fetch. The public `text` field is the
  concatenation of those `content` strings.
- **Review mutation lock** — New-review and scope-change turns release
  the `server_tasks.db` flock after candidate registration, instead of
  holding it for the whole Review run. A lock wait timeout on
  `/v1/query/route` is now HTTP 503 `Review mutation is busy`, not an
  uncaught ASGI 500 that the Web gateway mapped to 502.
- **Context Expert provider faults** — When `/v1/query/route` carries a
  conversation envelope, a routing-provider timeout is HTTP 504
  `upstream_timeout` and a non-timeout provider failure is HTTP 502
  `routing_upstream_failed`, both `stage=routing` and retryable. The
  V0 route already used those codes; the context path no longer
  surfaces them as an uncaught 500.

## [0.1.3] — 2026-07-17

Cross-surface capability and lifecycle release. It preserves the 0.1.2
GaussDB, Expert-routing, citation, and analyst-report contracts while adding
bounded interactive surfaces, revisioned background reports, capability
metadata, and interoperable progress across HTTP and MCP transports.
Full commit range: `1f8f628..4bc66a3`.

> **Release boundary.** This entry records the Bot-side implementation and
> offline gate only. Web/Go consumer integration, DBA and operations evidence,
> live backend acceptance, and production rollout remain separately owned
> checks; they are not implied by this changelog or the local test suite.

### Added

- **Streamed answer persistence** — ordinary HTTP streams for ChatAgent,
  KnowledgeAgent, ReviewAgent, and BriefGeneAgent settle the run registry
  with the accumulated answer (soft-capped via
  `PHYTOMNI_STREAM_ANSWER_MAX_BYTES`) so history overlay no longer replaces
  visible replies with a `"[streamed]"` placeholder.
- **Revisioned DeepGenome snapshots** — owner-scoped list and single-run
  reads share the local snapshot projection, expose monotonic report
  revisions, and select the latest usable `intermediate_report` until a
  successful final synthesis publishes `final_report`; read paths never
  probe remote child jobs.
- **Bounded direct A2UI ingress** — direct action requests reject oversized,
  duplicate-key, deeply nested, or over-shaped payloads before graph entry,
  while response bytes and identifiers remain capped under the existing
  flag-off default.
- **Agent capability descriptors** — `/v1/agents` keeps its legacy fields and
  adds immutable descriptors for the ten canonical slugs, including streaming,
  interactive, report-state, artifact, and degraded-outcome support so
  consumers do not infer capabilities from names.
- **Persistent SQLite checkpointer** — ReviewAgent graph pause points are
  stored in a local `checkpoints.db` beside the run/task registry so a
  human-approval interrupt can survive an HTTP API restart.
- **ReviewAgent human-in-the-loop approval** — ReviewAgent can pause with
  `status: "input_required"` and surface the drafted review for approval
  before finalizing.
- **HTTP resume endpoint** — `POST /v1/runs/{thread_id}/resume` accepts an
  approval payload (`approved` plus optional `edits`) and resumes the paused
  ReviewAgent thread through the shared resume kernel.
- **A2UI confirm surfaces for streamed chat (flag-gated)** —
  `PHYTOMNI_A2UI_ENABLED` gates ChatAgent SSE short-circuit into a
  `phyto.a2ui` confirm widget (`input_required` settle) and the companion
  `POST /v1/runs/{run_id}/a2ui-actions` resume route; default off.
- **Review A2UI dual-transport (flag-gated)** — the same
  `PHYTOMNI_A2UI_ENABLED` flag projects `phyto.a2ui` confirm surfaces
  onto Review HTTP pauses (non-stream and minimal `stream: true` pause
  SSE). Review resumes through `/resume` or `/a2ui-actions`; both attach
  `result.a2ui` on success. A2UI uplink maps to `{approved, edits: null}`
  because the Review graph does not consume `edits`.
- **A2UI Chat confirm contract fixtures** — `docs/contracts/a2ui/` ships
  copyable downlink / uplink / success / error JSON goldens for the
  Chat confirm surface, locked by offline shape tests for Web/Go
  consumers. No runtime behavior change; Go `/a2ui-actions` passthrough
  remains a Web-gateway follow-up.
- A2UI Chat form/choice heuristic surfaces (flag-gated) plus Review
  confirm and Chat form/choice contract goldens under
  `docs/contracts/a2ui/`.
- **A2UI rich props + multi-turn (P4-1e)** — shared Surface Author
  (domain templates → LLM → thin fallback), Review form/choice resume
  (R3 fields/selected/cancelled), and bounded N=2 Chat/Review re-entry
  with `multi_turn/round2_downlink.json` (`sfc-contract-2`). Flag
  default remains off.
- **Opt-in A2A v1 server core** — `PHYTOMNI_A2A_ENABLED` gates a public
  `/.well-known/agent-card.json` and authenticated `/a2a` JSON-RPC surface.
  Phase 2 accepts `SendMessage` and `SendStreamingMessage` with text/object
  data parts, maps the ten MCP tools into skills, projects results into A2A
  task/artifact messages, and streams status/text/data updates over SSE;
  owner-scoped task polling is available through `GetTask`; Review and
  A2UI-backed Chat pauses project `INPUT_REQUIRED` schemas and same-task
  `SendMessage` resumes validate generation through the shared kernel;
  push and extended-card methods remain explicitly unsupported.
- **Outbound interop foundation (opt-in)** — `PHYTOMNI_INTEROP_ENABLED` loads
  an operator-owned MCP/A2A target registry with credentials kept in the
  separate encrypted settings surface. The authenticated,
  read-only `/v1/interop/capabilities` route discovers sanitized MCP tools and
  A2A skills through a hardened no-proxy/no-redirect boundary with DNS/IP
  policy, per-target TTL/single-flight caching, and target-level failure
  isolation. The route remains discovery-only; execution requires the
  per-request Research/Design controls documented below.
- **Research/Design outbound delegation (opt-in)** — `InSilicoResearchAgent`
  and `DigitalDesignAgent` accept `interop_mode=off|auto|required` plus
  operator-registered `interop_targets`. `off` is local-only, `auto` may use
  MCP/A2A evidence and records `degraded_interop=true` when it falls back
  locally, and `required` fails closed when external evidence is unavailable.
  Formatted metadata exposes only bounded target/kind/capability/status/latency
  summaries; A2A `input-required` pauses resume through the agent graph.
- **Explicit user memory lifecycle (opt-in)** — `MEMORY_ENABLED` gates
  user-scoped CRUD, live-record export, digest-only mutation audit, TTL-aware
  purge, and bounded read-only Chat/Knowledge recall. Writes derive the
  namespace from the authenticated API key; expired/foreign records are not
  returned, failed writes fail closed, and graph read failures degrade to an
  observable empty result. The store is local single-instance SQLite only;
  there is no autonomous `langmem` writer, embedding store, or semantic index.
- **MCP elicitation with graceful degrade** — stdio ReviewAgent calls ask
  elicitation-capable clients for approval and auto-approve when a legacy
  client lacks that capability.
- **Client elicitation capability** — the MCP client surface advertises
  elicitation support so interactive ReviewAgent approval can flow over
  stdio.
- **Progress-event vocabulary** — `mcp/progress_events.py` ships a
  `ProgressEvent` TypedDict and `emit_progress()` helper that writes through
  the LangGraph stream writer; outside a runnable context it is a silent
  no-op, so the same node body works streamed and unstreamed. Protocol
  adapters may preserve the field set as progress metadata, while task
  lifecycle state is mapped independently (`phase` is not an A2A
  `TaskState`).
- **SSE `phyto.progress` Custom frame** — `_stream_graph_agent` now drives
  `astream(stream_mode=["custom","updates","values"], subgraphs=True)` and
  projects custom `phyto.progress` ticks from any namespace into a new
  `Custom` frame interleaved before the terminal `TextMessage`. The
  namespace filter is a correctness gate: `updates`/`values` project
  parent-only (`ns == ()`), preventing child-subgraph nodes from spuriously
  firing `StepStarted` or clobbering the parent's terminal state.
- **MCP stdio progress notifications** — when the client supplies a
  `progressToken` and the tool is a graph agent (Knowledge / Review / Data
  / BriefGene per `_GRAPH_PROGRESS_TOOLS`), `dispatch_tool` drives the
  graph and forwards each `phyto.progress` tick to
  `send_progress_notification`; the terminal payload is byte-identical to
  the blocking path. Calls without a token keep the blocking
  `invoke_tool_enveloped` path unchanged.
- **BriefGene SSE streaming** — `BriefGeneAgent` joins
  `_STREAM_CAPABLE_TOOLS` and routes through the shared graph streaming
  primitive; `DataAgent` stays out (no `phyto-data` model alias → no SSE
  entry point).
- **Node emission points** — 15+ graph reduce / section / post nodes across
  Knowledge / Review / Data / BriefGene call `emit_progress` at the top of
  their bodies (before the first `await`), so the tick fires when the stage
  begins. Emission is a pure side-effect; under blocking `ainvoke` it
  no-ops.
- **Client `progress_callback`** — `PhytomniMcpClient.call_tool` accepts an
  optional `progress_callback` and threads it to the MCP SDK's
  `ClientSession.call_tool`.
- **Phase maps for Data + BriefGene** — `mcp/streaming_phases.py:_PHASE_MAP`
  gains DataAgent (3 phases: retrieving / rewriting / querying) and
  BriefGeneAgent (4 phases + 4 section nodes sharing `analyzing`).
- **DRY `initial_brief_gene_state`** — the 40-key initial-state literal
  that previously lived inline in `BriefGeneAgent.arun` is extracted to a
  shared module-level function called by both `arun` and
  `brief_gene_stream_seed`, preventing the two from drifting.
- **CLI structured call output** — `phytomni call` prints `formatted.answer`
  first, then optional DataAgent TSV (`tabular`), cited-tool references,
  and a one-line async task metadata summary (`task_id` / `output_dir` /
  `status` / `failures`).
- **CLI capped failure detail lines** — when `formatted.metadata.failures`
  is non-empty, `phytomni call` also prints up to three capped
  `label: message` lines beneath the metadata summary (still keeps
  `failures={count}`).
- **OBS upload docs for `obs_file_list`** — README and
  `docs/reference/mcp-tools.md` document `POST /v1/files` → `obs_path` →
  tool args (links the existing HTTP file-upload contract).
- **Dependency-window CI** — a Python 3.12 job installs direct dependency
  floors with uv `lowest-direct` and runs the complete offline suite, while
  the existing 3.12–3.14 jobs continue to resolve highest-compatible
  versions.
- **Release version contract** — package `__version__`, installed wheel
  metadata, `pyproject.toml`, and FastAPI OpenAPI metadata are locked to the
  same 0.1.3 value; a wheel build now produces the expected 0.1.3 artifact.

### Changed

- `_stream_graph_agent` upgraded from 2-tuple `(mode, chunk)` to 3-tuple
  `(namespace, mode, chunk)` astream unpacking with `subgraphs=True`. All
  dependent test fakes upgraded consistently.
- `dispatch_tool` now detects `progressToken` + graph-tool and routes
  through `_drive_stdio_progress`; the no-token fallback is byte-identical
  to the prior blocking path.
- **Async submit formatting** — missing or blank `task_id` (Analyst /
  Network / DeepGenome / InSilico) formats as a failed submit instead of
  `Task created successfully:None`. Empty DigitalDesign task lists project
  universal `failures` into metadata and the answer when present.
- **DataAgent NL2SQL error wording** — user-visible failures now say
  `Failed to query SQL database (upstream gateway timeout or HTTP error)`
  (and the existing after-retries / no-attempts suffixes). Docs note that
  Bot does not change upstream platform SLA; shared HTTP mid-retry stays
  quiet with traceback only on exhaustion.
- **Install guidance** — README prefers Linux for local runs, positions
  `uv` as primary and conda as secondary (still needs
  `pip install -e ".[dev,demo]"`), and notes that pydub/ffmpeg
  `RuntimeWarning` is safe to ignore.
- **DeepGenome GetTaskStatus probe** — umbrella rows without
  `source_task_id` reconcile from the local registry + live-task heal
  only (no remote jobs API call with the local umbrella id). One-shot
  `phytomni call` still cannot keep DeepGenome background work alive;
  use `phytomni-api` or a persistent MCP session (documented in
  [CLI Reference](docs/reference/cli.md)).
- **LangGraph compatibility window** — the tested dependency range is now
  `langgraph>=1.2.0,<1.3`, with public API smoke coverage for Runtime context,
  stream writers, interrupt/resume, checkpointers, and Store injection.
- **Capability boundary** — README and upgrade notes now distinguish the
  opt-in A2A server core from outbound MCP/A2A and cross-session Store memory,
  which remain later-phase work.

______________________________________________________________________

## [0.1.2] — 2026-07-07

Feature + hardening release on top of `0.1.1`. Cuts the BI query path over to a
direct GaussDB connection, adds an autonomous Expert routing endpoint, enriches
cited references with full bibliographic fields, drives the graph agents through
SSE streaming, and lands four backend reliability knobs. **One required operator
action at deploy** — provision the new `GAUSS_DSN` secret (see
[`docs/ops/upgrading.md`](docs/ops/upgrading.md)); everything else
is additive. Full commit range: `adaa874..1f8f628`.

### Added

- **Direct GaussDB BI path** — a `GAUSS_DSN` secret + `asyncpg` dependency and a
  `gauss_query` seam with a per-event-loop connection pool replace the HTTP BI
  backend. In relay mode the `/v1/relay/bi/query` route is
  server-side-terminated
  (the operator runs the query locally; no credential is forwarded to the
  child).
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
- **GaussDB pool per-query timeout** — bounds a query that would otherwise hold
  a
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
- **`RERANK_CONCURRENCY` deployment knob** — a per-event-loop semaphore
  throttles
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
