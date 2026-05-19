# Phytomni-Bot

Phytomni-Bot is a Python 3.12-3.14 Model Context Protocol (MCP) server
for plant science research. It exposes a set of domain-specific tools for chat,
literature retrieval, natural-language SQL, bioinformatics workflow
orchestration, review generation, gene function analysis, in-silico research
decomposition, gene networks, and digital design.

The server package lives under `src/mcp_server_phytomni`. The main MCP
entrypoint is `src/mcp_server_phytomni/server.py`. A companion CLI client
package lives under `src/mcp_client_phytomni` and is shipped from the same
wheel; after `pip install -e .` it exposes a `phytomni` console script.

## Current Status

- The wheel packages two libraries: `mcp_server_phytomni` (the MCP server)
  and `mcp_client_phytomni` (a stdio CLI client and tool-result formatters
  for applications that drive the server).
- The MCP server currently exposes 11 tools.
- Several domain agents are LangGraph `StateGraph` workflows with compiled
  apps invoked through a shared runner.
- MCP dispatch lives in `mcp/app.py`, `mcp/schemas.py`, and
  `mcp/handlers.py`; `server.py` remains the module startup entrypoint.
- Domain wrappers live under `agents/<domain>/` packages and reuse agent
  instances through a non-secret runtime registry where safe.
- Legacy root Python modules such as `knowledge_agents.py`, `utils.py`, and
  `tool_handlers.py` are not compatibility surfaces.
- `func_cache` provides a tested SQLite-backed sync/async cache decorator.
- Default pytest runs are offline, secret-free, and network-blocked.
- CI runs `black`, `ruff`, `flake8`, `mypy`, `pyright`, `pylint`, default
  offline `pytest`, `yamllint`, and `jsonlint`.
- Ships small synthesized demo fixtures under [`demo_data/`](demo_data/)
  and a live business-layer E2E suite under [`e2e/`](e2e/) that drives
  every MCP tool against real backends through `PhytomniMcpClient`. See
  the [Demo Data & E2E](#demo-data--e2e) section for the per-tool payloads
  and the manual run command.

## Available MCP Tools

For tools that include `obs_file_list`, pass an empty list (`[]`) when no
document upload is needed.

| Tool | Main module | Required arguments | Purpose |
| --- | --- | --- | --- |
| `ChatAgent` | `agents/chat/service.py` | `user_query`, `obs_file_list` | General plant science chat with optional document context. |
| `KnowledgeAgent` | `agents/knowledge/agent.py` | `user_query`, `obs_file_list` | Literature retrieval and RAG-based synthesis. |
| `DataAgent` | `agents/data/agent.py` | `user_query` | Natural-language SQL query rewriting and database search. |
| `AnalystAgent` | `agents/analyst/agent.py` | `goal_description`, `data_list`, `obs_file_list` | Bioinformatics workflow retrieval, planning, submission, and status handling. |
| `ReviewAgent` | `agents/review/agent.py` | `user_query`, `obs_file_list` | Multi-step literature review and deep research generation. |
| `BriefGeneAgent` | `agents/brief_gene/agent.py` | `user_query` | Concise gene function report from BI annotations and literature context. |
| `DeepGenomeAgent` | `agents/deep_genome/agent.py` | `species_code`, `gene_id` | Multi-omics gene function analysis. |
| `InSilicoResearchAgent` | `agents/research/agent.py` | `user_query`, `data_list`, `obs_file_list` | Decompose papers or research goals into computational tasks. |
| `DigitalDesignAgent` | `agents/design/agent.py` | `species`, `gene_id`, `obs_file_list` | Protein and promoter design workflows. |
| `GeneNetworkAgent` | `agents/network/agent.py` | `species`, `to_id`, `obs_file_list` | Gene network analysis for species and trait ontology IDs. |
| `GetTaskStatus` | `runtime/task_manager.py` | `task_id` | Non-blocking status lookup for a previously submitted async task. |

### Submit-then-poll for async tools

`AnalystAgent`, `DeepGenomeAgent`, `DigitalDesignAgent`,
`GeneNetworkAgent`, and `InSilicoResearchAgent` submit work to a
backend and return a `task_id` without waiting for completion. Use the
two-step pattern:

1. Call the submit tool; keep the returned `task_id`.
2. Call `GetTaskStatus` with that `task_id` to check progress. It is
   non-blocking — it reads the local task registry and merges one live
   platform status check, and never waits, so it is safe to poll on
   your own cadence. An unrecorded id returns `status: "unknown"`.

## Architecture

```text
src/mcp_server_phytomni/
  server.py                  Compatibility startup module for MCP launchers
  mcp/
    app.py                   MCP server registration, dispatch, and serving
    schemas.py               Public tool names and request schemas
    handlers.py              Runtime handlers and config expansion
  agents/
    chat/                    Chat service workflow
    knowledge/               Retrieval, reranking, and synthesis workflow
    data/                    NL2SQL and data query workflow
    analyst/                 Analyst graph, storage, and wrapper
    review/                  Deep research review workflow
    brief_gene/              Brief gene function workflow
    deep_genome/             Deep genome graph and helpers
    research/                In-silico research decomposition workflow
    design/                  Digital design workflow
    network/                 Gene network workflow
    environment/             Environment workflow (not MCP-bridged)
    evolution/               Evolution workflow (not MCP-bridged)
    shared/
      analysis.py            Cross-agent Analyst-backed analysis helpers
      analysis_storage.py    Cross-agent storage and OBS path helpers
      options.py             Shared chat/submit kwargs builders
      parallel_dispatch.py   Shared StateGraph builder for parallel agents
  runtime/
    langgraph_runner.py      Shared LangGraph invocation helpers
    agent_registry.py        Reusable agent registry keyed by safe config
    task_manager.py          Task lifecycle helper
    workflow_mixins.py       Reusable workflow mixin helpers for nodes
  common/
    http.py                  JSON POST retry helpers
    prompts.py               Prompt template and JSON file loading
    responses.py             LLM response parsing helpers
    docs.py                  Retrieved document formatting helpers
    lists.py                 Small list helpers
  auth/
    iam.py                   IAM token loading helper
  storage/
    obs_storage.py           OBS object naming and upload helpers
    path_policy.py           Runtime path and ID policy
    downloads.py             OBS/obsfs download and conversion helpers
    scratch.py               Obsfs-first per-run scratch directory resolver
  config/
    defaults.py              Non-secret defaults, agent config classes,
                             and Pydantic schemas for static datasets
    settings.py              Environment and secret loading
    overrides.py             Wrapper argument to config override helpers
    data_loaders.py          Validated loaders for the static datasets
    .prompts.yaml            Prompt templates
    species_data_list.json   Species metadata
    region_map.json          Region metadata
  func_cache/                SQLite-backed function cache package
src/mcp_client_phytomni/
  client.py                  PhytomniMcpClient, PhytomniToolRouter, and
                             response models for stdio-driven applications
  main.py                    `phytomni` CLI entry point
  tool_result_formatters.py  Citation, doc dedup, and follow-up formatters
```

### MCP Boundary

`mcp/app.py`, `mcp/schemas.py`, and `mcp/handlers.py` own the public MCP
surface:

- Pydantic request models for each tool in `mcp/schemas.py`.
- JSON schema generation for `tools/list`.
- Argument validation and MCP-compliant `INVALID_PARAMS` errors.
- Tool name to handler routing through `dispatch_tool`.
- MCP `TextContent` response serialization.

`mcp/handlers.py` owns runtime adaptation from MCP requests to agent calls:

- Loading default config and sensitive config.
- Expanding config values into compatibility wrapper arguments.
- Calling wrapper functions or service methods in the domain agent packages.

Keep public tool names and request schemas stable unless a change is planned
as an API migration. The old root Python module paths are intentionally not
kept as compatibility shims; import code should use the package paths shown
above.

### LangGraph Agents

Most complex agents are implemented as LangGraph workflows:

- `StateGraph` defines the workflow state and node transitions.
- Agent classes compile a graph into `self.app`.
- `runtime/langgraph_runner.py` centralizes `RunnableConfig`, `thread_id`,
  checkpointer defaults, and async graph invocation.
- Wrapper functions in the domain packages build config objects, preserve
  tool-facing signatures, and call agent classes rather than exposing graph
  internals.
- `runtime/agent_registry.py` reuses agent instances by explicit non-secret
  config fingerprints. Secret values are omitted from cache keys.

The following wrapper function names are intentionally kept inside their new
domain packages:

- `rewrite_nl2sql`
- `multi_retrieve_generate`
- `retrieve_generate` (single-repo convenience helper; delegates to
  `multi_retrieve_generate` with a one-key `repo_id_dict`. Public for
  direct importers; not registered as an MCP tool.)
- `deep_research`
- `brief_gene_function`
- `gene_function`
- `design_module`
- `network_analysis`
- `in_silico_research`
- `retrieve_plan_submit`

`mcp/handlers.py` should call these wrappers or a shared service layer; it
should not duplicate graph construction or reach into private graph builders.

### Configuration

Non-secret defaults live in `config/defaults.py`. Secrets and environment
loading live in `config/settings.py` via `pydantic-settings`.

Wrapper override logic lives in `config/overrides.py`. It maps historical
keyword arguments such as `model_url`, `coder_api_key`, `output_dir`, and
`deepgenome_data` onto the appropriate config or sensitive config fields
without changing public wrapper signatures.

For tests, `PHYTOMNI_TESTING=1` disables real `.env` file loading and lets
the test suite inject dummy secrets. Do not use that mode for real service
runs.

The three bundled static datasets (`species_data_list.json`,
`region_map.json`, `.prompts.yaml`) are validated by Pydantic schemas in
`config/defaults.py` (`SpeciesDataIndex`, `RegionMap`, `PromptTemplates`)
and consumed through `config/data_loaders.py`. If you edit the JSON or
YAML directly, the next run of the offline test suite will fail with a
`pydantic.ValidationError` whenever the new shape diverges from the
schema, so prefer adjusting the schema and data together.

### Caching

`src/mcp_server_phytomni/func_cache` provides:

- deterministic key building from function signatures,
- pickle serialization with optional zlib compression,
- SQLite storage with TTL support,
- database-backed locks,
- sync and async decorators with concurrent-miss protection,
- `exclude_params` support for clients, sessions, checkpointers, and secrets,
- `cache_info()` and `cache_clear()` helpers.

The default cache database path is `PHYTOMNI_CACHE_DB` when set, otherwise
`.cache/phytomni/func_cache.sqlite`. This path stays on local disk regardless
of obsfs availability — SQLite over a network filesystem can deadlock under
WAL locking, so the func_cache database is intentionally excluded from the
scratch resolver's obsfs routing. The first low-risk integrations cache
template file reads, static JSON/text metadata reads, `get_data_list`, and
`network_to_string`, all with explicit TTLs and file fingerprints where local
files are involved. Rendered prompts are not persisted because parameters may
contain user queries, uploaded document content, or retrieved text.
Second-wave retrieval integrations use short TTLs for knowledge retrieval,
gene literature retrieval, and DeepGenome BI gene lookup/annotation helpers;
API tokens, HTTP clients, semaphores, and checkpointers are excluded from
cache keys. `nl2sql` is not cached by default because `dialog_id` may carry
session context. Cache database files such as `.func_cache.db*`, `*.sqlite*`,
and WAL/SHM sidecars are ignored by git.

This is separate from `runtime/agent_registry.py`. The registry only reuses
in-memory agent instances and compiled LangGraph apps for matching non-secret
configuration; it does not cache LLM responses, external API responses, task
submissions, uploads, downloads, or polling results.

## Installation

### Requirements

- Python `>=3.12,<3.15`
- Linux is the primary supported runtime environment.
- `uv` is recommended for local development.
- A reasonably modern C toolchain (GCC ≥ 9 / glibc ≥ 2.28) **or**
  conda-forge prebuilt wheels. `numpy`/`pandas` are pulled in
  transitively by `markitdown[all]`; on an ancient compiler they fall
  back to a source build that fails. See
  [Building numpy/pandas from source on an old toolchain](#building-numpypandas-from-source-on-an-old-toolchain).

### Using uv

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .
uv pip install -e ".[dev]"
```

To regenerate the bundled `demo_data/` fixtures, drive the live e2e
suite, or run `./scripts/validate_local.sh` (which now verifies
`demo_data/` idempotency before pytest), also install the `[demo]`
extra:

```bash
uv pip install -e ".[dev,demo]"
```

Python 3.12 remains the default local example, while Python 3.13 and 3.14
are also supported and covered by CI compatibility checks.

The project also keeps `dependency-groups.dev` for uv-oriented workflows, but
the CI and standard editable install path use `[project.optional-dependencies]`
with `.[dev]`.

### Using conda or mamba

```bash
conda env create -f environment.yml
conda activate phytomni-bot
pip install -e .
pip install -e ".[dev]"
```

> On a host with an old system compiler (GCC < 9), `conda env create`
> can try to build `numpy`/`pandas` from source and fail. Install them
> as conda-forge prebuilt wheels first — see
> [Building numpy/pandas from source on an old toolchain](#building-numpypandas-from-source-on-an-old-toolchain).

## Configuration

Copy the example environment file and fill in real credentials:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

Expected variables:

```bash
DOMAIN_NAME=your_domain_name
USER_NAME=your_username
USER_PASSWORD=your_password
ACCESS_KEY_ID=your_access_key_id
SECRET_ACCESS_KEY=your_secret_access_key
BASE_URL=your_llm_base_url
MODEL_ID=your_model_id
API_KEY=your_api_key
CODER_URL=your_coder_base_url
CODER_MODEL=your_coder_model
CODER_API_KEY=your_coder_api_key
EMBED_URL=your_embed_base_url
EMBED_MODEL=your_embed_model
EMBED_API_KEY=your_embed_api_key
BI_TOKEN=your_bi_token
```

`EMBED_URL`, `EMBED_MODEL`, and `EMBED_API_KEY` are required;
`BI_TOKEN` is optional and defaults to empty.

Legacy `AccessKeyID` and `SecretAccessKey` environment names remain accepted
for compatibility, but new local configuration should use the uppercase names.

Never commit `.env`, API keys, OBS credentials, model keys, generated cache
databases, or local virtual environments.

### Distribution to Trusted Customers

Phytomni-Bot ships to a small number of trusted customers as a Docker
image that consumes **our** Huawei resources and **our** LLM quota, so the
plaintext `.env` must never enter the image. Instead, each customer gets a
per-customer encrypted envelope:

1. **Build time (operator):** seal that customer's `.env` with their
   license key:

   ```bash
   python scripts/encrypt_env.py \
     --input src/mcp_server_phytomni/config/.env \
     --license-key "<per-customer-license-key>" \
     --output src/mcp_server_phytomni/config/.env.encrypted
   ```

   The output is an AES-256-GCM blob (`PHYBOT01` magic, PBKDF2-derived
   key). Bake `.env.encrypted` — never the plaintext `.env` — into that
   customer's image. The `.dockerignore` enforces this for any future
   Dockerfile.

2. **Runtime (customer):** the customer supplies only their license key,
   from either source — the `PHYTOMNI_LICENSE_KEY` environment variable
   (e.g. `docker -e`), **or** a `config/.license_key` file dropped on the
   host at deploy time (or mounted as a Docker volume / k8s secret). The
   environment variable wins when both are present, so an operator can
   override without re-provisioning the file. The bot derives the key,
   decrypts the envelope into the process environment at startup, and
   never writes the plaintext to disk.

   The license key is delivered **out-of-band** and must NEVER be baked
   into the image: an image that carried both `.env.encrypted` and the
   key would make an image leak equivalent to a plaintext leak, defeating
   the envelope. `.dockerignore` (and `.gitignore`) therefore exclude
   `.license_key` just as they exclude plaintext `.env`.

A leaked license key compromises one customer's envelope only — rebuild
and redistribute with a rotated key, no fleet-wide exposure.

**Threat scope.** Encryption blocks casual inspection (`docker history`,
`docker export`, `cat .env`). It does **not** stop a motivated operator
with `gcore`, `py-spy`, or `tcpdump` on their own host; defending against
that requires the request-forwarder relay tracked in the deferred plan,
not this envelope.

### OBSFS Storage

The storage helpers prefer the obsfs mount at `/obs/phytomni` for OBS-backed
file operations. When that mount or an individual filesystem operation is not
usable, the code falls back to the existing OBS SDK path and credentials.
There is no feature flag to enable obsfs; availability is detected at runtime.

When obsfs is available, uploaded documents are converted directly from the
mounted source path, generated Analyst metadata is written directly under
`/obs/phytomni/agent_data/tmp_data/`, and DeepGenome reads completed Analyst
result directories in place instead of downloading them to a local staging
directory.

Per-run scratch directories — handler-level temporary file roots
(`server_dir`), Analyst download caches, DeepGenome's downloaded-result and
synthesized-report directories — are resolved through `storage/scratch.py`.
With obsfs available they land under
`/obs/phytomni/agent_data/user_data/<user>/runs/<date>/<run>/<scope>/{downloads,tmp}/`;
without it they fall back to run-scoped subdirectories of `TEMP_DIR`,
`DOWNLOAD_PATH`, or `DEEPGENOME_OUT` so the repo root no longer accumulates
flat `.out` and `.temp` directories. Use `storage.scratch.resolve_scratch_dir`
for new agent-level call sites and `mcp.handlers.scratch_server_dir` for new
handler wrappers.

For root or sudo-enabled runtime checks:

```bash
sudo stat /obs/phytomni
sudo test -r /obs/phytomni && sudo test -w /obs/phytomni
```

If those checks fail, normal execution should still work through the OBS SDK
fallback as long as the configured OBS credentials are valid.

## Running the Server

After editable installation:

```bash
python -m mcp_server_phytomni.server
```

From the repository without relying on the active environment path:

```bash
PYTHONPATH=src python -m mcp_server_phytomni.server
```

The server uses MCP stdio transport.

### `phytomni` CLI

After `pip install -e .`, the `phytomni` console script is available for
quick stdio-driven inspection of any MCP server that points at this
package (or another one through `--server`):

```bash
phytomni list-tools
phytomni call ChatAgent '{"user_query": "Explain C3 photosynthesis.", "obs_file_list": []}'
```

The CLI lives in `src/mcp_client_phytomni/main.py` and delegates to
`PhytomniMcpClient` and the tool-result formatters in the same package.

## HTTP API

The same agents are also reachable over an authenticated HTTP API that
runs as a **separate process** beside (never replacing) the stdio MCP
server. It reuses the MCP handler layer through a single shared
invocation seam, so MCP behavior is unchanged.

### Start the service

```bash
phytomni-api                 # binds ApiConfig API_HOST/API_PORT
python -m mcp_server_phytomni.api.server   # equivalent
```

`ApiConfig` (in `config/defaults.py`) carries non-secret, env-overridable
settings; the SQLite stores are **local-only** (network filesystems
deadlock under SQLite WAL):

| Setting | Env (either name) | Default |
| --- | --- | --- |
| API bind host | `API_HOST` | `127.0.0.1` |
| API bind port | `API_PORT` | `8080` |
| API key store | `API_KEYS_DB_PATH` / `PHYTOMNI_API_KEYS_DB` | `.cache/phytomni/api_keys.sqlite` |
| Run ownership store | `API_RUNS_DB_PATH` / `PHYTOMNI_API_RUNS_DB` | `.cache/phytomni/api_runs.sqlite` |
| Backend task registry | `API_TASKS_DB_PATH` / `PHYTOMNI_TASKS_DB` | `server_tasks.db` |
| Per-key req/min | `API_RATE_LIMIT_PER_MIN` | `120` (`<= 0` disables) |

### Per-user API keys

Inbound auth is a per-user key, fully separate from the outbound LLM
`API_KEY`. Keys are stored only as PBKDF2-HMAC-SHA256 hashes with a
per-key salt; the plaintext is shown once at creation and never
recoverable. Manage them with the admin CLI:

```bash
phytomni-api-key create --user-id alice --name laptop [--expires-days 90]
phytomni-api-key list   [--user-id alice]
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

Send the key as either header:

```
Authorization: Bearer ptm_...
X-API-Key: ptm_...
```

Every response carries an `X-Request-Id`; errors on native routes use a
unified envelope `{"error": {"type", "code", "message", "request_id"}}`.
Over-budget callers get `429` with `Retry-After`. Streaming is not
supported (`stream: true` → `400`).

### Endpoints

- `GET /healthz` — liveness (no auth, no dependencies).
- `GET /readyz` — readiness (no auth; checks the local store dirs are
  writable without creating anything).
- `GET /v1/models` — lists the OpenAI-compatible model ids.
- `POST /v1/chat/completions` — OpenAI-compatible; `model` selects a
  chat-like agent: `phyto-chat`, `phyto-knowledge`, `phyto-review`,
  `phyto-brief-gene`. `doc_list` / `follow_up_questions` are surfaced as
  extra top-level keys; `phyto-brief-gene` rejects a non-empty
  `obs_file_list`.

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-chat","messages":[{"role":"user","content":"Explain C3 photosynthesis."}]}'
```

Native per-agent runs and long-running task polling
(`POST /v1/agents/{agent}/runs`, `GET /v1/runs/{run_id}`) are added in a
later change and documented when they land. The MCP stdio server remains
`python -m mcp_server_phytomni.server` and is unaffected.

## MCP Client Example

```python
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_server_phytomni.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print([tool.name for tool in tools.tools])

            result = await session.call_tool(
                "ChatAgent",
                {
                    "user_query": "Explain C3 photosynthesis.",
                    "obs_file_list": [],
                },
            )
            print(result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
```

Example payloads:

```json
{
  "tool": "DataAgent",
  "arguments": {
    "user_query": "What are the homologous genes of AT1G75370 in wheat?"
  }
}
```

```json
{
  "tool": "BriefGeneAgent",
  "arguments": {
    "user_query": "AT1G01010"
  }
}
```

```json
{
  "tool": "AnalystAgent",
  "arguments": {
    "goal_description": "Run peak calling for rice ATAC-seq data.",
    "data_list": {
      "/obs/phytomni/path/to/sample_1.fq.gz": "ATAC-seq read 1",
      "/obs/phytomni/path/to/sample_2.fq.gz": "ATAC-seq read 2"
    },
    "obs_file_list": []
  }
}
```

## Demo Data & E2E

The repository ships small, fully-synthesized fixtures under
[`demo_data/`](demo_data/) and a live business-layer end-to-end suite
under [`e2e/`](e2e/). Together they let a freshly-cloned checkout drive
every MCP tool against real backends with a single command.

### What's in `demo_data/`

```
demo_data/
├── README.md                         # auto-generated index
├── manifest.json                     # tool → payload + fixture map
├── payloads/                         # 11 JSON payloads, one per MCP tool
├── docs/                             # plant-science brief MD/PDF + xlsx
├── sequences/                        # short Arabidopsis FASTA
└── scripts/generate_demo_data.py     # idempotent regenerator
```

Every fixture is regenerated by
[`demo_data/scripts/generate_demo_data.py`](demo_data/scripts/generate_demo_data.py)
and pinned to deterministic timestamps so re-running produces a clean
working tree (enforced by `./scripts/validate_local.sh`).

### Per-tool demo payloads

| Tool | Kind | Payload | Demo summary |
| --- | --- | --- | --- |
| ChatAgent | sync | [chat_agent.json](demo_data/payloads/chat_agent.json) | C3 photosynthesis explainer (no upload). |
| KnowledgeAgent | sync | [knowledge_agent.json](demo_data/payloads/knowledge_agent.json) | Wheat drought-tolerance evidence query. |
| DataAgent | sync | [data_agent.json](demo_data/payloads/data_agent.json) | NL2SQL homology lookup for AT1G75370. |
| ReviewAgent | sync | [review_agent.json](demo_data/payloads/review_agent.json) | Multi-section sorghum drought review. |
| BriefGeneAgent | sync | [brief_gene_agent.json](demo_data/payloads/brief_gene_agent.json) | Concise gene-card for AT1G01010. |
| AnalystAgent | async | [analyst_agent.json](demo_data/payloads/analyst_agent.json) | ATAC-seq peak-calling on rice replicates. |
| DeepGenomeAgent | async | [deep_genome_agent.json](demo_data/payloads/deep_genome_agent.json) | Deep gene-function analysis (ath, AT1G75370). |
| InSilicoResearchAgent | async | [in_silico_research_agent.json](demo_data/payloads/in_silico_research_agent.json) | Reproducibility tasks from the brief PDF. |
| DigitalDesignAgent | async | [digital_design_agent.json](demo_data/payloads/digital_design_agent.json) | Protein + promoter design for AT1G75370. |
| GeneNetworkAgent | async | [gene_network_agent.json](demo_data/payloads/gene_network_agent.json) | Trait-network analysis for rice (TO:0000207). |
| GetTaskStatus | sync | [get_task_status.json](demo_data/payloads/get_task_status.json) | Non-blocking status poll for a submitted task id. |

OBS paths inside the committed payloads use the placeholder prefix
`/obs/phytomni/demo/`. The e2e suite's [`conftest.py`](e2e/conftest.py)
rewrites every placeholder at session start to the per-run upload
location, so concurrent runs cannot collide.

### Calling a tool from the CLI

```bash
phytomni call ChatAgent "$(cat demo_data/payloads/chat_agent.json)"
phytomni call DataAgent "$(cat demo_data/payloads/data_agent.json)"
```

### Running the live business E2E

The `e2e/` suite is **not** part of default CI. It is invoked manually
after the user has filled in their `.env`:

```bash
cp src/mcp_server_phytomni/config/.env.example \
   src/mcp_server_phytomni/config/.env
# fill secrets in .env
uv pip install -e ".[dev,demo]"
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/ -v
```

Tunables (set as environment variables):

| Variable | Purpose | Default |
| --- | --- | --- |
| `PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS` | Per-call submit timeout for async tools. | 1800 |
| `PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS` | Polling deadline for one async task. | 600 |
| `PHYTOMNI_E2E_TASKS_DB` | Override `server_tasks.db` path. | repo root |
| `PHYTOMNI_E2E_RUN_KA_UPLOAD` | Set to `1` to also run the KnowledgeAgent uploaded-document variant. Skipped by default: with an attached document the retrieve→rerank fan-out is backend-bound and can exceed 30 min when the retrieval tier is degraded. The no-upload KnowledgeAgent test always runs. | unset (skipped) |

See [`e2e/README.md`](e2e/README.md) for the full layout, entry-point
rationale (client-stdio is the only path; handler-direct and
wrapper-direct are already covered by `tests/server/` and
`tests/agents/`), and the async polling caveats.

## Development

### Offline Tests

Default pytest is configured to skip `integration` and `network` tests:

```bash
uv run pytest
```

Equivalent explicit form:

```bash
uv run pytest -m "not integration and not network"
```

Coverage report form used by CI:

```bash
uv run pytest \
  --cov=mcp_server_phytomni \
  --cov-report=term-missing \
  --cov-report=xml
```

Tests are grouped by directory and automatically marked as `unit`, `server`,
`agent`, or `integration`. `integration` tests stay skipped unless
`PHYTOMNI_RUN_INTEGRATION=1` is set. Tests marked `network` stay skipped unless
`PHYTOMNI_ALLOW_NETWORK=1` is set.

The test suite currently includes unit coverage for:

- `func_cache` serializer, key builder, storage, lock, and decorator behavior,
- config defaults and sensitive settings test mode,
- config override helpers for wrapper argument compatibility,
- prompt/template helpers and `split_list`,
- MCP tool schemas and dispatch routing,
- package boundary tests that prevent legacy root module imports,
- shared LangGraph runner and agent registry behavior,
- wrapper override propagation for digital design, gene network, and
  in-silico research entrypoints,
- offline mock-LLM smoke tests for ChatAgent upload context and follow-up
  question handling,
- offline fake-graph smoke tests for Data, Knowledge, BriefGene, Review,
  InSilicoResearch, GeneNetwork, DigitalDesign, Analyst, and DeepGenome
  agents.

### Lint and Type Checks

Run the same gates as CI:

```bash
uv run black --check .
uv run ruff check .
uv run flake8 src tests --count --statistics
uv run mypy src
pyright src
PYTHONPATH=src uv run pylint --persistent=no src tests
uv run pytest \
  --cov=mcp_server_phytomni \
  --cov-report=term-missing \
  --cov-report=xml
uv run yamllint .
git ls-files '*.json' | while IFS= read -r file; do
  jsonlint "$file" --quiet
done
```

In restricted local sandboxes, `uv run --no-sync ...` can be used to reuse an
already installed environment when plain `uv run` tries to rebuild the package
or access a read-only uv cache.

Pylint now runs without global rule disables. Local Pylint waivers are guarded
by the style tests and are reserved for documented framework boundaries.

### Local Quality Gate

`./scripts/validate_local.sh` runs the full gate (secret scan, compileall,
whitespace, black, ruff, flake8, mypy, pyright, pylint, yamllint, jsonlint,
`demo_data/` idempotency, then `pytest`) over every tracked file — the same
checks as CI and the `.githooks/pre-push` hook. A `Makefile` wraps it and adds
a **scoped** gate (`scripts/scoped_gate.sh`) that runs those same tools and
flags but only over the files in the active change region, so parallel work is
not blocked by unrelated whole-tree failures:

```bash
make help        # list targets
make full        # full validate_local.sh (CI parity, no scoping)
make precommit   # scoped gate over the staged index
make prepush     # scoped gate over @{upstream}..work-tree (else merge-base main)
make scoped      # alias of prepush (range scope)
make push        # git push with an SSH keepalive (the hook still runs)
```

The scoped gate mirrors `validate_local.sh` exactly but skips any tool whose
file kind did not change, runs `demo_data/` idempotency only when `demo_data/`
changed, and always runs the whole-tree structural tests
(`test_style_naming` / `test_pytest_layers` / `test_package_boundaries`)
whenever any `.py` changed. `make push` uses an SSH keepalive instead of
`--no-verify`, so the pre-push hook still runs. A `PHYTOMNI_SCOPED_GATE=1`
pre-push opt-in (run the scoped gate instead of the full gate on push) is
planned as a follow-up.

### Config Normalization

Prompt YAML and static JSON metadata are kept in deterministic, lint-friendly
formats. Normalize prompt YAML after editing nested prompt content:

```bash
python scripts/normalize_yaml.py sort \
  src/mcp_server_phytomni/config/.prompts.yaml
```

Normalize all config JSON files, or pass explicit JSON paths:

```bash
python scripts/normalize_json.py
python scripts/normalize_json.py \
  src/mcp_server_phytomni/config/species_data_list.json \
  src/mcp_server_phytomni/config/region_map.json
```

### CI

`.github/workflows/lint.yml` runs:

- `black --check .`
- `ruff check .`
- `flake8 src tests`
- `mypy src`
- `pyright src`
- `pylint --persistent=no $(git ls-files '*.py')` (its job installs
  `[dev,demo]` so the `demo_data/scripts/generate_demo_data.py`
  imports of `reportlab` and `openpyxl` resolve)
- `pytest --cov=mcp_server_phytomni`
- `yamllint .`
- `jsonlint "$file" --quiet` for every tracked JSON file

## Repository Hygiene

- Follow [STYLE.md](STYLE.md) for naming, docstrings, copyright headers, and
  import organization.
- Keep public MCP tool names and schemas stable.
- Keep generated caches and SQLite cache databases out of git.
- `.env.encrypted` (the `PHYBOT01` envelope) is the only `.env*` artifact
  permitted inside a shipped image; plaintext `.env` and its variants must
  never enter a build context (enforced by `.dockerignore` and the
  `scan_secrets.py` envelope check).
- Prefer structured parsing and Pydantic validation over ad hoc string
  handling.
- Add or update focused tests for behavior changes.
- Do not cache LLM generations, task submission, polling, uploads, downloads,
  or other side-effecting operations unless a later design explicitly allows
  it.
- Regenerate `demo_data/` only through
  [`demo_data/scripts/generate_demo_data.py`](demo_data/scripts/generate_demo_data.py)
  and keep its output byte-deterministic; the
  `./scripts/validate_local.sh` idempotency check fails on any drift.

## Dependencies

Runtime dependencies are declared in `pyproject.toml` and include:

- `mcp`
- `langgraph`
- `langchain-core`
- `openai`
- `httpx`
- `pydantic`
- `pydantic-settings`
- `python-dotenv`
- `pyyaml`
- `markitdown[all]`
- `esdk-obs-python`

Development dependencies include:

- `black`
- `ruff`
- `flake8`
- `mypy`
- `pyright` through `npx` or global npm install in CI
- `pylint`
- `pytest`
- `pytest-asyncio`
- `pytest-cov`
- `yamllint`

Demo / live-E2E dependencies (`[project.optional-dependencies].demo`) cover
the `demo_data/` regenerator and the e2e suite's bundled imports:

- `reportlab`
- `openpyxl`

CI also installs Node-based `jsonlint` with npm for tracked JSON validation.

### Dependency Policy

This repository does not commit `uv.lock`. The lock file may exist locally, but
it stays ignored and must not be staged.

CI installs from `pyproject.toml` using the configured official PyPI index.
Dependency specifiers should stay as lower bounds (`>=`) unless a specific
package needs a documented compatibility pin. Because CI does not use a
committed lock file, dependency upgrades must update the relevant lower bounds
in `pyproject.toml`.

When changing dependencies:

- update `project.dependencies` for runtime packages,
- keep `[project.optional-dependencies].dev` and `[dependency-groups].dev`
  version-aligned for development tools,
- preserve compatibility with Python 3.12, 3.13, and 3.14 unless the
  supported range is explicitly changed,
- run `uv sync --extra dev --group dev`,
- run `uv pip check --python .venv/bin/python`,
- run the full lint, type, test, YAML, and JSON gates before committing.

## Troubleshooting

### Missing configuration

If no configuration source is found, startup raises a `RuntimeError`
that enumerates the three accepted provisioning paths:

1. `PHYTOMNI_TESTING=1` — the test suites inject dummy secrets.
2. A license key — `PHYTOMNI_LICENSE_KEY=<key>` or a
   `config/.license_key` file — with a `.env.encrypted` envelope
   beside `config/` — the customer-image path (see
   [Distribution to Trusted Customers](#distribution-to-trusted-customers)).
3. A plaintext `config/.env` — the local developer path:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

A wrong `PHYTOMNI_LICENSE_KEY` (or a corrupted envelope) raises
`SecretEnvelopeError` and aborts startup rather than booting with
empty secrets.

### Building numpy/pandas from source on an old toolchain

`numpy` and `pandas` are not direct dependencies; they are pulled in
transitively by `markitdown[all]` (used for the document-upload
agents). The project deliberately keeps open `>=` ranges and ships no
`uv.lock` (see the Dependency Policy in [CLAUDE.md](CLAUDE.md)), so on
a host with an old compiler `pip`/`conda` resolves the newest releases
and tries to **compile them from source**, failing with errors like
`gcc: error: unrecognized command line option` or a C99/C11 standard
error.

This is a host-toolchain limitation, not a project defect — the fix is
to provide prebuilt binaries, **not** to pin versions. Pick one
supported path:

**Option 1 — modern toolchain.** Use a host or container with
GCC ≥ 9 and glibc ≥ 2.28. Recent manylinux wheels then install with
no local compilation.

**Option 2 — conda-forge prebuilt wheels (no system compiler
needed).** Install the heavy binary deps as conda-forge wheels
*before* the editable install, so `pip` sees them already satisfied
and skips the source build entirely:

```bash
conda create -n phytomni-bot python=3.12
conda activate phytomni-bot
# Prebuilt wheels — no source build, no system GCC required:
conda install -c conda-forge numpy pandas lxml
pip install -e ".[dev]"
```

Add any other C-extension dependency that still fails to the
`conda install -c conda-forge ...` line. Do **not** pin these
versions in `pyproject.toml`/`environment.yml`: the open-range
dependency policy is intentional, and the toolchain — not the
project — is what to upgrade.

### Tool Argument Validation

Tool schemas are strict. If a tool requires `obs_file_list`, pass `[]` when no
files are used.

### Network and External Services

Most real agent calls depend on external services: LLM endpoints, retrieval
services, NL2SQL services, OBS, BI APIs, or bioinformatics platforms. Default
pytest deliberately blocks network access; mark tests with `network` only when
they intentionally call real services.

## License

This project is licensed under the terms specified in [LICENSE](LICENSE).

## Authors

- Shang Xie <xieshang0608@gmail.com>
- Yichao Mao <maoyc_0316@163.com>
- Hu Li <lihu0628@qq.com>
- Xiaofeng Gu <guxiaofeng@caas.cn>

Copyright (c) Biotechnology Research Institute, Chinese Academy of
Agricultural Sciences. 2024-2026. All rights reserved.
