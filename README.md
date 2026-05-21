# Phytomni-Bot

Phytomni-Bot is a Python 3.12-3.14 Model Context Protocol (MCP) server
for plant science research. It exposes domain-specific agents for chat,
literature retrieval, natural-language SQL, bioinformatics workflow
orchestration, review generation, gene function analysis, in-silico
research decomposition, gene networks, and digital design.

The package ships two importable libraries from one wheel:

- `mcp_server_phytomni`: the MCP server and authenticated HTTP API.
- `mcp_client_phytomni`: a stdio client, CLI, and tool-result formatters.

## Quick Start

Install with `uv`:

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev,demo]"
```

Copy the local environment template and fill in real credentials:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

Run the MCP stdio server:

```bash
python -m mcp_server_phytomni.server
```

Or inspect tools through the bundled CLI:

```bash
phytomni list-tools
phytomni call ChatAgent '{"user_query": "Explain C3 photosynthesis.", "obs_file_list": []}'
```

The HTTP API runs as a separate process:

```bash
phytomni-api
phytomni-api-key create --user-id alice --name laptop
```

See [HTTP API](docs/http-api.md) for authentication, endpoint contracts,
run polling, retention, and OpenAI-compatible chat examples.

## Available MCP Tools

For tools that include `obs_file_list`, pass an empty list (`[]`) when no
document upload is needed.

| Tool                    | Kind  | Required arguments                               | Purpose                                                                       |
| ----------------------- | ----- | ------------------------------------------------ | ----------------------------------------------------------------------------- |
| `ChatAgent`             | sync  | `user_query`, `obs_file_list`                    | General plant science chat with optional document context.                    |
| `KnowledgeAgent`        | sync  | `user_query`, `obs_file_list`                    | Literature retrieval and RAG-based synthesis.                                 |
| `DataAgent`             | sync  | `user_query`                                     | Natural-language SQL query rewriting and database search.                     |
| `ReviewAgent`           | sync  | `user_query`, `obs_file_list`                    | Multi-step literature review and deep research generation.                    |
| `BriefGeneAgent`        | sync  | `user_query`                                     | Concise gene function report from BI annotations and literature context.      |
| `AnalystAgent`          | async | `goal_description`, `data_list`, `obs_file_list` | Bioinformatics workflow retrieval, planning, submission, and status handling. |
| `DeepGenomeAgent`       | async | `species_code`, `gene_id`                        | Multi-omics gene function analysis.                                           |
| `InSilicoResearchAgent` | async | `user_query`, `data_list`, `obs_file_list`       | Decompose papers or research goals into computational tasks.                  |
| `DigitalDesignAgent`    | async | `species`, `gene_id`, `obs_file_list`            | Protein and promoter design workflows.                                        |
| `GeneNetworkAgent`      | async | `species`, `to_id`, `obs_file_list`              | Gene network analysis for species and trait ontology IDs.                     |
| `GetTaskStatus`         | sync  | `task_id`                                        | Non-blocking status lookup for a previously submitted async task.             |

Async tools submit work to a backend and return a `task_id`. Poll that id
through `GetTaskStatus`; the lookup is non-blocking and returns
`status: "unknown"` for an unrecorded id.

`AnalystAgent` also deduplicates identical submissions. It hashes
`(goal_description, data_list, obs_file_list)` and reuses a prior in-flight
or succeeded task instead of submitting the same scientific question again.
Failed and cancelled rows are ignored so retries still create fresh work.

## Architecture

The MCP entrypoint is `src/mcp_server_phytomni/server.py`. The public MCP
surface lives in `src/mcp_server_phytomni/mcp/`, domain implementations live
under `src/mcp_server_phytomni/agents/<domain>/`, and shared runtime,
storage, auth, configuration, and cache helpers live in `runtime/`,
`storage/`, `auth/`, `config/`, `common/`, and `func_cache/`.

For the full package map, dispatch boundary, LangGraph wrapper policy,
configuration ownership, and cache policy, see
[Architecture](docs/architecture.md).

## Configuration

Local development uses `src/mcp_server_phytomni/config/.env`.
Trusted-customer images use an encrypted `.env.encrypted` envelope plus a
runtime license key. The project never commits plaintext `.env` files, API
keys, OBS credentials, generated cache databases, or local virtual
environments.

Common local variables:

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

`EMBED_URL`, `EMBED_MODEL`, and `EMBED_API_KEY` are required. `BI_TOKEN`
is optional and defaults to empty. Legacy `AccessKeyID` and
`SecretAccessKey` names remain accepted, but new local configuration should
use the uppercase names above.

See [Deployment and Storage](docs/deployment.md) for encrypted customer
distribution, license-key resolution, OBSFS-first storage, scratch path
layout, and configuration troubleshooting.

## Demo Data and Live E2E

The repository ships deterministic demo fixtures under
[`demo_data/`](demo_data/) and a live business-layer E2E suite under
[`e2e/`](e2e/). The fixtures include one JSON payload per MCP tool plus
small markdown, PDF, xlsx, and FASTA uploads.

Call demo payloads through the CLI:

```bash
phytomni call ChatAgent "$(cat demo_data/payloads/chat_agent.json)"
phytomni call DataAgent "$(cat demo_data/payloads/data_agent.json)"
```

Run the live suite only after `.env` is fully configured:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
  uv run pytest e2e/ -v
```

The live suite is not part of default CI. See
[`e2e/README.md`](e2e/README.md) for the full layout, timeout tunables,
upload handling, and async polling caveats.

## Development

Default tests are offline, secret-free, and network-blocked:

```bash
uv run pytest
```

The full local gate mirrors CI and the pre-push hook:

```bash
./scripts/validate_local.sh
```

For faster feedback on the active change region:

```bash
make scoped
```

The gate covers secret scanning, compile checks, whitespace, Python
format/lint/type checks, shell/YAML/JSON/Markdown/TOML checks,
`demo_data/` idempotency, and offline pytest. See
[Development](docs/development.md) for the full command matrix, CI scope,
dependency policy, config normalization, and common troubleshooting.

## Documentation

- [Architecture](docs/architecture.md): package layout, MCP dispatch,
  LangGraph wrappers, configuration ownership, and cache policy.
- [HTTP API](docs/http-api.md): service startup, per-user keys,
  endpoints, polling, retention, and response shape.
- [Deployment and Storage](docs/deployment.md): encrypted customer
  configuration, OBSFS fallback behavior, and scratch directory policy.
- [Development](docs/development.md): local gates, CI, dependency policy,
  demo fixtures, E2E commands, and troubleshooting.
- [STYLE.md](STYLE.md): naming, docstrings, imports, compatibility rules,
  and repository-specific code style.

## License

This project is licensed under the terms specified in [LICENSE](LICENSE).

## Authors

- Shang Xie <xieshang0608@gmail.com>
- Yichao Mao <maoyc_0316@163.com>
- Hu Li <lihu0628@qq.com>
- Xiaofeng Gu <guxiaofeng@caas.cn>

Copyright (c) Biotechnology Research Institute, Chinese Academy of
Agricultural Sciences. 2024-2026. All rights reserved.
