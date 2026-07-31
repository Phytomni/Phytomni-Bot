# Architecture

Phytomni-Bot is organized around one public MCP surface and a set of
domain packages behind it. The top-level launcher stays small, while
schema validation, dispatch, formatting, runtime helpers, storage, and
domain workflows live in dedicated packages.

## Package Layout

```text
src/mcp_server_phytomni/
  server.py                  Compatibility startup module for MCP launchers
  mcp/
    app.py                   MCP server registration, dispatch, and serving
    schemas.py               Public tool names and request schemas
    handlers.py              Runtime handlers and config expansion
    handler_support.py       Reusable handler assembly helpers (kwargs builders)
    result_formatting.py     Tool-response formatter at the dispatch boundary
  api/
    app.py                   FastAPI application factory for the HTTP service
    server.py                uvicorn launcher for the external HTTP API
    auth.py                  SQLite-backed API key store and inbound auth
    resolver
    admin_auth.py            Service-token auth for /v1/api-keys management
    routes
    keys.py                  Admin CLI for the per-user API key store
    openai_mapping.py        OpenAI-compatible chat mapping helpers
    ratelimit.py             In-process per-key sliding-window rate limiter
    file_upload.py           Handler for POST /v1/files multipart upload
    schemas.py               HTTP API request and response schemas
    relay/                   Credential-injecting customer relay subpackage
  interop/
    models.py                Immutable operator-owned MCP/A2A target policy
    registry.py              Feature-gated target and credential-ref loader
    security.py              Endpoint, DNS, IP, and origin/path policy
    http_transport.py        Hardened HTTPX transport for external peers
    mcp_client.py            Official external MCP adapter boundary
    capabilities.py          Sanitized, non-executable capability DTOs
    cache.py                 Monotonic TTL and per-target single-flight cache
    a2a_discovery.py         External A2A Agent Card discovery
    a2a_client.py            External A2A send/stream client
    a2a_mapping.py           Bounded external A2A event mapping
  agents/
    chat/                    Chat service workflow
    knowledge/               Retrieval, reranking, and synthesis workflow
    data/                    NL2SQL and data query workflow
    analyst/                 Analyst graph and wrapper
    review/                  Deep research review workflow
    brief_gene/              Brief gene function workflow
    deep_genome/             Deep genome graph and helpers
    research/                In-silico research decomposition workflow
    design/                  Digital design workflow
    network/                 Gene network workflow
    environment/             Environment LangGraph subgraph (region VCI), not
    MCP-bridged
    evolution/               Evolution LangGraph subgraph (taxonomy-driven), not
    MCP-bridged
    shared/
      analysis.py            Cross-agent Analyst-backed analysis helpers
      analysis_storage.py    Cross-agent storage and OBS path helpers
      intermediate_state.py  LangGraph final-state lifter to phytomni_state
      options.py             Shared chat and submit kwargs builders
      parallel_dispatch.py   Shared StateGraph builder for parallel agents
      sql.py                 Shared SQL literal escaping helper
  graphs/
    loader.py                Declarative graph manifest loader (Pydantic +
    allowlist)
    allowlist.py             Allowed subgraph identifiers for the loader
    adapters.py              Helpers for embedding compiled subgraphs in parents
    manifests/               JSON subgraph composition manifests
  runtime/
    langgraph_runner.py      Shared LangGraph invocation helpers
    agent_registry.py        Reusable agent registry keyed by safe config
    request_context.py       Per-request user, run, and recorder-degraded
    contextvars
    memory/                   Explicit user memory models, local SQLite store,
    and read accessor
    run_registry.py          HTTP API parent-run registry
    submit_recorder.py       Submit-handler chokepoint: persists run + task
    rows; logs and flags degraded_tracking on SQLite write failure
    task_manager.py          Task lifecycle helper
    task_reconcile.py        Per-task status reconciliation against backend
    terminal_artifacts.py    Terminal-payload artifact persistence helpers
    workflow_mixins.py       Reusable workflow mixin helpers for nodes
  common/
    cli.py                   Shared CLI entry-point helpers
    docs.py                  Retrieved document formatting helpers
    http.py                  JSON POST retry helpers
    httpx_client.py          Lifecycle-managed shared AsyncClient factory
    lists.py                 Small list helpers
    logging_config.py        Package-level logging setup with PHYTOMNI_DEBUG
    prompts.py               Prompt template and JSON file loading
    reasoning_content.py     Helpers for OpenAI reasoning_content shaping
    responses.py             LLM response parsing helpers
  auth/
    iam.py                   IAM token loading helper
  storage/
    obs_storage.py           OBS object naming and upload helpers
    path_policy.py           Runtime path and ID policy
    downloads.py             OBS and obsfs download/conversion helpers
    scratch.py               Obsfs-first per-run scratch directory resolver
    uploads.py               HTTP /v1/files multipart upload OBS bridge
  config/
    defaults.py              Non-secret defaults, agent config classes,
                             and Pydantic schemas for static datasets
    settings.py              Environment and secret loading
    secret_envelope.py       AES-256-GCM envelope for the encrypted
    .env.encrypted
    relay_mode.py            Customer relay-mode flag detection (leaf module)
    overrides.py             Wrapper argument to config override helpers
    data_loaders.py          Validated loaders for the static datasets
    .prompts.yaml            Prompt templates
    species_data_list.json   Species metadata
    region_map.json          Region metadata
    to_ontology.json         Plant Trait Ontology catalog for the network
    resolver
  func_cache/                SQLite-backed function cache package
src/mcp_client_phytomni/
  client.py                  PhytomniMcpClient, PhytomniToolRouter, and
                             response models for stdio-driven applications
  main.py                    `phytomni` CLI entry point
  tool_result_formatters.py  FormattedToolResult model and parse-only shim
```

## DeepGenome execution and report boundary

DeepGenome uses one local umbrella and normalized child rows rather than
exposing upstream task topology:

```text
owner request
    -> atomic reservation: runs + umbrella tasks + BriefGene section
    -> in-process coordinator
       -> deep_genome_sections (logical report sections)
       -> deep_genome_remote_tasks (submitted id + effective polling id)
       -> bounded remote polling and per-transition snapshot writes
    -> report_revision N: intermediate_report
    -> successful synthesis: report_revision N+1: final_report
```

`agents/deep_genome/coordinator.py` normalizes submission acknowledgements and
owns the bounded polling loop. `agents/deep_genome/dispatch.py` and
`runtime/deep_genome_transitions.py` persist state through
`runtime/deep_genome_store.py`; the store reserves the owner run, umbrella
task, and required BriefGene row in one transaction and applies compare-and-
swap report revisions. A successful BriefGene profile is the launch barrier;
optional analysis rows may fail independently, but synthesis needs at least
one usable summary. The public snapshot is projected by
`runtime/deep_genome_report_snapshot.py` and read by MCP `GetTaskStatus`, HTTP
`GET /v1/runs/{run_id}`, and the HTTP-backed CLI. Those read paths do not poll
the analysis platform.

The coordinator is deliberately process-local. A service restart does not
resume after process restart; reconciliation settles an orphaned nonterminal
umbrella at the fixed restart-failure boundary while retaining its latest
intermediate report. This release has no cross-process durable worker, no
DataAgent HTTP streaming surface, and no claim that production migration or
Web/Go acceptance is complete. Operational rollback and external evidence are
owned by the deployment process and are not implied by this architecture note.

## MCP Boundary

`mcp/app.py`, `mcp/schemas.py`, `mcp/handlers.py`, and
`mcp/result_formatting.py` own the public MCP surface:

- Pydantic request models for each tool in `mcp/schemas.py`.
- JSON schema generation for `tools/list`.
- Argument validation and MCP-compliant `INVALID_PARAMS` errors.
- Tool name to handler routing through `dispatch_tool`.
- Shared raw invocation through `invoke_tool_raw(name, arguments)`.
- Shared enveloped invocation through `invoke_tool_enveloped(name, arguments)`
  returning a `ToolResultEnvelope(formatted, raw)`; `invoke_tool_formatted`
  is a back-compat shim over the same seam returning only the formatted half.
- Citation/document formatting, DataAgent `tabular` field, DigitalDesign
  `output_dirs` tuple, and task-submission metadata in
  `mcp/result_formatting.py`. The same module's `_sanitize_raw` strips
  credential-pattern keys from the raw payload before it reaches the
  envelope.
- MCP `TextContent` response serialization. The dispatch seam emits
  `{"formatted": {...}, "raw": {...}}` so the MCP stdio and HTTP API
  surfaces ship the identical envelope shape.

`mcp/handlers.py` adapts public tool requests to domain packages by loading
configuration, expanding compatibility wrapper arguments, and calling agent
wrappers or service methods. It should not duplicate graph construction or
reach into private graph builders.

Keep public MCP tool names and request schemas stable unless a change is
planned as an API migration. Legacy root Python modules such as
`knowledge_agents.py`, `data_agents.py`, `tool_handlers.py`, and `utils.py`
are not compatibility surfaces.

## LangGraph Agents

Most complex agents are implemented as LangGraph workflows:

- `StateGraph` defines workflow state and node transitions.
- Agent classes compile a graph into `self.app`.
- `runtime/langgraph_runner.py` centralizes `RunnableConfig`, `thread_id`,
  checkpointer defaults, and async graph invocation.
- Wrapper functions in domain packages build config objects, preserve
  tool-facing signatures, and call agent classes.
- `runtime/agent_registry.py` reuses agent instances by explicit non-secret
  config fingerprints. Secret values are omitted from cache keys.

The following wrapper function names remain public inside their domain
packages:

- `rewrite_nl2sql`
- `multi_retrieve_generate`
- `retrieve_generate`
- `review_agent_function`
- `brief_gene_function`
- `gene_function`
- `design_module`
- `network_analysis`
- `in_silico_research`
- `retrieve_plan_submit`

## Configuration Ownership

Non-secret defaults live in `config/defaults.py`. Secrets and environment
loading live in `config/settings.py` via `pydantic-settings`.

Wrapper override logic lives in `config/overrides.py`. It maps historical
keyword arguments such as `model_url`, `coder_api_key`, `output_dir`, and
`deepgenome_data` onto config or sensitive-config fields without changing
public wrapper signatures.

For tests, `PHYTOMNI_TESTING=1` disables real `.env` file loading and lets
the test suite inject dummy secrets. Do not use that mode for real service
runs.

The bundled static datasets (`species_data_list.json`, `region_map.json`,
`.prompts.yaml`) are validated by Pydantic schemas in `config/defaults.py`
and consumed through `config/data_loaders.py`. If their shape changes,
adjust the schema and data together.

## Explicit Memory Boundary

`runtime/memory/models.py` defines storage-neutral, bounded memory records and
the per-user policy. `runtime/memory/sqlite.py` is a single-instance local
SQLite store with additive schema migration, WAL, optimistic revisions, TTL
filtering, digest-only mutation audit, and user-scoped export/purge helpers.
It is not a distributed store and must not be placed on a network filesystem.

The HTTP API is the only memory write surface. `POST`, `PUT`, and `DELETE`
derive the namespace from the authenticated API key and reject caller-supplied
owners. `GET /v1/memories/export` is a live, owner-scoped portability read;
`GET /v1/memories/audit` is service-token-only and exposes metadata/digests, not
content. `expires_at` is the retention boundary, and purge deletions reuse the
digest-only delete audit record.

`runtime/memory/accessor.py` is the only graph-facing seam. It lazily opens
SQLite only when the feature flag and authenticated request namespace permit
it, returns bounded newest-first records, and degrades failed reads to an
observable empty result without logging user ids or content. Agents do not
write memory autonomously: there is no `langmem` writer, embedding store, or
semantic index. Memory text is untrusted reference context injected into
prompts, never instruction authority.

## Outbound Interoperability Boundary

Outbound interop is an operator-owned, opt-in boundary, not a second public
agent-dispatch path. `interop/registry.py` parses the target registry only
when `INTEROP_ENABLED=1`; requests can carry a target id but never a URL,
command, args, header, token, or credential reference. Target policy and
credential values are deliberately separate: immutable target models hold
origins, paths, allowlists, timeouts, and capability names, while
`SensitiveConfig.INTEROP_CREDENTIALS` resolves a `credential_ref` only after
the endpoint passes the security policy.

The existing `common.httpx_client.get_async_client` pool remains the boundary
for trusted, configured platform backends. External MCP and A2A peers must use
the separate interop transport, which disables environment proxies,
redirects, and transparent retries; resolves and validates DNS/IP results;
pins the connection to the validated address while preserving Host/SNI; and
applies response, idle, and total-time budgets. HTTPS is the default, and
private or special-use addresses require an explicit target CIDR allowlist.
Stdio is an explicit operator trust decision: only absolute fixed binaries,
fixed arguments, and a minimal environment allowlist are accepted.

Discovery produces immutable `InteropCapability` DTOs containing only target
id/kind, remote name, qualified name, description, and bounded JSON input
schema. `DiscoveryCache` stores successful DTO results with a monotonic
per-target TTL and coalesces concurrent misses; failures are shared with the
current waiters but are not retained as long-term negative cache entries. The
HTTP `/v1/interop/capabilities` route is read-only and returns deterministic
partial errors, so it never starts a tool or agent run. The cache holds no
client, transport, executable tool, credential, or peer payload, and there is
no persistent interop audit database.

External A2A cards are structurally validated against A2A/JSON-RPC policy and
operator allowlists. Unless a JWS trust key is explicitly configured, card
handling makes no cryptographic signature-verification claim. Research and
Design delegation are separate request-level Phase 4 seams: they consume
allowlisted capability DTOs only when `interop_mode=auto|required` is supplied,
never by inference from the discovery route. Their evidence is bounded and
untrusted, local Analyst/OBS submission remains the side-effect boundary, and
`required` fails closed instead of treating local fallback as peer success.

## Caching Policy

`src/mcp_server_phytomni/func_cache` provides deterministic key building,
pickle serialization with optional zlib compression, SQLite storage with
TTL support, database-backed locks, sync and async decorators,
concurrent-miss protection, `exclude_params`, `cache_info()`,
`cache_clear()`, and the `phytomni-cache` admin CLI.

The default cache database path is `PHYTOMNI_CACHE_DB` when set, otherwise
`.cache/phytomni/func_cache.sqlite`. This path stays on local disk even when
obsfs is available because SQLite over a network filesystem can deadlock
under WAL locking. Cache database files such as `.func_cache.db*`,
`*.sqlite*`, and WAL/SHM sidecars are ignored by git.

The cache memoizes the most basic non-local primitives, keyed strictly on
semantic inputs. Infrastructure, secrets, URLs, sessions, checkpointers,
timeouts, retry policy, `dialog_id`, and chat streaming flags are excluded
from keys.

Current cache scope:

- Chat LLM completions through the stable keyword adapter
  `agents/chat/service.py:run_phyto_chat_cached`; the decorated cache
  primitive is `_run_chat_completion_cached` and keys only on the semantic
  message list and response format.
- Knowledge retrieval across `_multi_retrieve`, `_retrieve_cached`, and
  `_retrieve_scope_docs`.
- NL2SQL at `agents/data/nl2sql.py:_execute_nl2sql_cached`.

Every cached primitive uses `func_cache.LONG_TTL_SECONDS`, currently about
90 days, because remote LLM/GPU concurrency is scarce. Local file loads
(templates, JSON/text metadata) and pure local compute such as
`network_to_string` are not cached: re-reading a local file or recomputing
a formatter is cheap relative to the SQLite roundtrip. Rendered prompts are
not persisted because parameters may contain user queries, uploaded document
content, or retrieved text.

The cache does not memoize task submission, polling, uploads, or downloads.
Duplicate analysis submissions instead reuse the prior remote `task_id`
through `tasks.input_fingerprint` and `TaskManager.get_task_by_fingerprint`
(not through `func_cache`). The shared helpers live in
`runtime/task_dedup.py` and cover both analyst entry points: the top-level
`retrieve_plan_submit` wrapper and the `submit_analyst_via_subgraph`
dispatch seam every sub-agent (design / network / research / deep_genome /
environment / evolution) funnels through. Because the local `tasks.status`
column is written once at submit and never advanced, a fingerprint hit is
verified against the live remote status before reuse
(`task_ops.probe_live_status` plus `verify_live_status`): a succeeded or
in-flight task is reused — a polling caller may only reuse a terminal task —
while a confirmed-dead task is written back to `failed` and resubmitted. The
seam writes its own fingerprint row at submit time, and `TaskManager.record`
`COALESCE`s `input_fingerprint` so the per-tool run recorder cannot clobber
the dedup key on a later write.

The fingerprint is content-keyed and intentionally tenant-agnostic
(`goal_description` + `data_list` + `obs_file_list`, never the authenticated
user), so an identical question dedupes across tenants — a deliberate cache
objective. Results are written to the tenant-neutral key
`agent_data/shared/<fingerprint>/output/` (via
`storage/path_policy.shared_output_key`), so no submitter's `user_id` appears
in the path and a cross-tenant reuse never exposes a prior caller's namespace.

A dedup hit does not hand the caller the prior tenant's `task_id`. Instead it
mints a fresh caller-owned task id (and a corresponding run row) and records it
with `tasks.source_task_id` holding the prior tenant's remote task id.
`source_task_id` is used server-side only by
`runtime/task_reconcile.py: reconcile_task` to probe live status
(`probe_id = row["source_task_id"] or task_id`); it is never returned to the
client. The caller therefore receives
their own run id and task id at HTTP 202, exactly like a fresh submission —
there is no `dedup_hit`/`id=null` passthrough for reuse on this path.

The OBS relay grants read access to `agent_data/shared/<fp>/` only when the
path carries a full 64-hex SHA-256 fingerprint segment (enforced by a compiled
regex in `_require_tenant_prefix` / `_require_output_prefix`). The bare
`agent_data/shared/` root is rejected, so a caller cannot enumerate other
tenants' fingerprints. A 64-hex fingerprint is unguessable, so possession of it
proves possession of the inputs that produced the result — no per-user segment
is required for this access class.

`runtime/agent_registry.py` is separate. It reuses in-memory agent instances
and compiled LangGraph apps for matching non-secret configuration, but it
does not cache LLM responses, external API responses, task submissions,
uploads, downloads, or polling results.
