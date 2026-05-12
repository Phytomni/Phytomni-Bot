# Phytomni-Bot

Phytomni-Bot is a Python 3.12-3.14 Model Context Protocol (MCP) server
for plant science research. It exposes a set of domain-specific tools for chat,
literature retrieval, natural-language SQL, bioinformatics workflow
orchestration, review generation, gene function analysis, in-silico research
decomposition, gene networks, and digital design.

The package lives under `src/mcp_server_phytomni`. The main MCP entrypoint is
`src/mcp_server_phytomni/server.py`.

## Current Status

- The project is packaged as `mcp_server_phytomni` with a `src/` layout.
- The MCP server currently exposes 10 tools.
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
    environment/             Environment workflow
    evolution/               Evolution workflow
    shared/                  Cross-agent analysis, storage, and option helpers
  runtime/
    langgraph_runner.py      Shared LangGraph invocation helpers
    agent_registry.py        Reusable agent registry keyed by safe config
    task_manager.py          Task lifecycle helper
  common/
    http.py                  JSON POST retry helpers
    prompts.py               Prompt template loading
    responses.py             LLM response parsing helpers
    docs.py                  Retrieved document formatting helpers
    lists.py                 Small list helpers
  auth/
    iam.py                   IAM token loading helper
  storage/
    obs_storage.py           OBS object naming and upload helpers
    path_policy.py           Runtime path and ID policy
    downloads.py             OBS/obsfs download and conversion helpers
  config/
    defaults.py              Non-secret defaults and agent config classes
    settings.py              Environment and secret loading
    overrides.py             Wrapper argument to config override helpers
    .prompts.yaml            Prompt templates
    species_data_list.json   Species metadata
    region_map.json          Region metadata
  func_cache/                SQLite-backed function cache package
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
`.cache/phytomni/func_cache.sqlite`. The first low-risk integrations cache
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

### Using uv

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .
uv pip install -e ".[dev]"
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
BI_TOKEN=your_bi_token
```

Legacy `AccessKeyID` and `SecretAccessKey` environment names remain accepted
for compatibility, but new local configuration should use the uppercase names.

Never commit `.env`, API keys, OBS credentials, model keys, generated cache
databases, or local virtual environments.

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
- `pylint --persistent=no $(git ls-files '*.py')`
- `pytest --cov=mcp_server_phytomni`
- `yamllint .`
- `jsonlint "$file" --quiet` for every tracked JSON file

## Repository Hygiene

- Follow [STYLE.md](STYLE.md) for naming, docstrings, copyright headers, and
  import organization.
- Keep public MCP tool names and schemas stable.
- Keep generated caches and SQLite cache databases out of git.
- Prefer structured parsing and Pydantic validation over ad hoc string
  handling.
- Add or update focused tests for behavior changes.
- Do not cache LLM generations, task submission, polling, uploads, downloads,
  or other side-effecting operations unless a later design explicitly allows
  it.

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

### Missing `.env`

If the server raises a missing `.env` error, copy the example file and provide
real values:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

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
