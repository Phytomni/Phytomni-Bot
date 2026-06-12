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
    auth.py                  SQLite-backed API key store and inbound auth resolver
    admin_auth.py            Service-token auth for /v1/api-keys management routes
    keys.py                  Admin CLI for the per-user API key store
    openai_mapping.py        OpenAI-compatible chat mapping helpers
    ratelimit.py             In-process per-key sliding-window rate limiter
    file_upload.py           Handler for POST /v1/files multipart upload
    schemas.py               HTTP API request and response schemas
    relay/                   Credential-injecting customer relay subpackage
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
    environment/             Environment LangGraph subgraph (region VCI), not MCP-bridged
    evolution/               Evolution LangGraph subgraph (taxonomy-driven), not MCP-bridged
    shared/
      analysis.py            Cross-agent Analyst-backed analysis helpers
      analysis_storage.py    Cross-agent storage and OBS path helpers
      intermediate_state.py  LangGraph final-state lifter to phytomni_state
      options.py             Shared chat and submit kwargs builders
      parallel_dispatch.py   Shared StateGraph builder for parallel agents
      sql.py                 Shared SQL literal escaping helper
  graphs/
    loader.py                Declarative graph manifest loader (Pydantic + allowlist)
    allowlist.py             Allowed subgraph identifiers for the loader
    adapters.py              Helpers for embedding compiled subgraphs in parents
    manifests/               JSON subgraph composition manifests
  runtime/
    langgraph_runner.py      Shared LangGraph invocation helpers
    agent_registry.py        Reusable agent registry keyed by safe config
    request_context.py       Per-request user, run, and recorder-degraded contextvars
    run_registry.py          HTTP API parent-run registry
    submit_recorder.py       Submit-handler chokepoint: persists run + task rows; logs and flags degraded_tracking on SQLite write failure
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
    secret_envelope.py       AES-256-GCM envelope for the encrypted .env.encrypted
    relay_mode.py            Customer relay-mode flag detection (leaf module)
    overrides.py             Wrapper argument to config override helpers
    data_loaders.py          Validated loaders for the static datasets
    .prompts.yaml            Prompt templates
    species_data_list.json   Species metadata
    region_map.json          Region metadata
    to_ontology.json         Plant Trait Ontology catalog for the network resolver
  func_cache/                SQLite-backed function cache package
src/mcp_client_phytomni/
  client.py                  PhytomniMcpClient, PhytomniToolRouter, and
                             response models for stdio-driven applications
  main.py                    `phytomni` CLI entry point
  tool_result_formatters.py  FormattedToolResult model and parse-only shim
```

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

- Chat LLM completions at
  `agents/chat/service.py:run_phyto_chat_cached`.
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

`runtime/agent_registry.py` is separate. It reuses in-memory agent instances
and compiled LangGraph apps for matching non-secret configuration, but it
does not cache LLM responses, external API responses, task submissions,
uploads, downloads, or polling results.
