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

Local development and runs work best on Linux. The examples below use a
Unix shell.

Install with `uv` (recommended):

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev,demo]"
```

Conda or mamba is a secondary path: create the env from
`environment.yml`, then still run `pip install -e ".[dev,demo]"` so the
editable packages and pip-only extras (including `markitdown[all]`) are
installed. See [CONTRIBUTING.md](CONTRIBUTING.md) for the conda
commands. Keep the `environment.yml` Python range
(`python>=3.12,<3.15`); CI exercises 3.12–3.14.

If import-time tooling prints a pydub / ffmpeg `RuntimeWarning`, you can
ignore it — Phytomni-Bot does not require a system ffmpeg install for
normal MCP or HTTP use.

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

`phytomni call` prints `formatted.answer` first, then optional tabular /
references / task-metadata blocks. DeepGenome and other background
submit tools need a long-lived `phytomni-api` or MCP server session —
a one-shot `phytomni call` exits the server subprocess and cancels
in-process background work. Details: [CLI Reference](docs/reference/cli.md).

The HTTP API runs as a separate process:

```bash
phytomni-api
phytomni-api-key create --user-id alice --name laptop
```

See [CLI Reference](docs/reference/cli.md) for the installed commands and
[HTTP API](docs/reference/http-api.md) for authentication, endpoint contracts,
run polling, retention, OpenAI-compatible chat, human-in-the-loop review
resume, and flag-gated A2UI confirm/form/choice widgets
(`PHYTOMNI_A2UI_ENABLED`; Surface Author + Chat/Review N=2). Copyable
A2UI goldens for Web/Go consumers (`chat_confirm`, `review_confirm`,
`chat_form`, `chat_choice`, `review_form`, `review_choice`,
`multi_turn`) live under
[docs/contracts/a2ui/](docs/contracts/a2ui/README.md).

### Capability Boundary in 0.1.3

The 2026 capability audit is closed for all 14 dependency-underutilization
items: six gate rules, four reliability improvements, three dormant-asset
connections, and MCP stdio progress are shipped. Graph progress is available
on HTTP SSE and, for Knowledge / Review / Data / BriefGene, through MCP
`progressToken`. Review interrupt/resume uses a persistent local SQLite
checkpointer. A2UI Chat/Review widgets are shipped behind
`PHYTOMNI_A2UI_ENABLED`, which remains off by default.

The A2A server core is now available as an opt-in 0.1.3 surface. It remains
disabled unless `PHYTOMNI_A2A_ENABLED=1` and a public base URL are configured;
flag-off behavior is unchanged. Phase 2 supports the public Agent Card,
authenticated JSON-RPC `SendMessage`, `SendStreamingMessage` over SSE, and
owner-scoped `GetTask` polling. Cancellation, push notifications, extended
cards, outbound MCP/A2A, and cross-session Store memory remain later-phase
work.

| Capability                                | 0.1.3 status |
| ----------------------------------------- | ------------ |
| A2A Agent Card and `/a2a` server          | Opt-in core  |
| Calls to external MCP tools or A2A agents | Not shipped  |
| Cross-session LangGraph Store memory      | Not shipped  |

Internal `phyto.progress.phase` values are stage labels, not A2A task states.
The A2A status adapter maps task lifecycle state independently. No outbound-
interoperability or memory feature flag is active in this release.

## Available MCP Tools

For tools that include `obs_file_list`, pass an empty list (`[]`) when no
document upload is needed. See [MCP Tool Reference](docs/reference/mcp-tools.md)
for detailed argument semantics, async behavior, and demo payload links.
To attach a document, upload it with `POST /v1/files` on the HTTP API,
then put the returned `obs_path` into `obs_file_list`. Demo upload
samples live under [`demo_data/`](demo_data/). Details:
[MCP Tool Reference — Uploading documents](docs/reference/mcp-tools.md#uploading-documents-for-obs_file_list).

| Tool                    | Kind  | Required arguments                               | Purpose                                                                                                                                           |
| ----------------------- | ----- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ChatAgent`             | sync  | `user_query`, `obs_file_list`                    | General plant science chat with optional document context.                                                                                        |
| `KnowledgeAgent`        | sync  | `user_query`, `obs_file_list`                    | Literature retrieval and RAG-based synthesis.                                                                                                     |
| `DataAgent`             | sync  | `user_query`                                     | Natural-language SQL query rewriting and database search.                                                                                         |
| `ReviewAgent`           | sync  | `user_query`, `obs_file_list`                    | Multi-step literature review and deep research generation.                                                                                        |
| `BriefGeneAgent`        | sync  | `user_query`                                     | Rich gene preamble (introduction + Gene Profiles with Basic Genomic Information and four analytical sections) from BI annotations and literature. |
| `AnalystAgent`          | async | `goal_description`, `data_list`, `obs_file_list` | Bioinformatics workflow retrieval, planning, submission, and status handling.                                                                     |
| `DeepGenomeAgent`       | async | `species_code`, `gene_id`                        | Multi-omics gene function analysis.                                                                                                               |
| `InSilicoResearchAgent` | async | `user_query`, `data_list`, `obs_file_list`       | Decompose papers or research goals into computational tasks.                                                                                      |
| `DigitalDesignAgent`    | async | `species_code`, `gene_id`, `obs_file_list`       | Protein and promoter design workflows.                                                                                                            |
| `GeneNetworkAgent`      | async | `species_code`, `to_id`, `obs_file_list`         | Gene network analysis for species and trait ontology IDs.                                                                                         |
| `GetTaskStatus`         | sync  | `task_id`                                        | Non-blocking status lookup for a previously submitted async task.                                                                                 |

Async tools submit work to a backend and return a `task_id`. A missing
or blank `task_id` is formatted as a failed submit (not
`Task created successfully:None`). Poll a real id through
`GetTaskStatus`; the lookup is non-blocking and returns
`status: "unknown"` for an unrecorded id.

Identical analysis submissions are deduplicated by a content fingerprint
over `(goal_description, data_list, obs_file_list)`. The shared
`runtime/task_dedup.py` helpers cover both the top-level `AnalystAgent` and
the dispatch seam that `DigitalDesignAgent`, `GeneNetworkAgent`,
`InSilicoResearchAgent`, and `DeepGenomeAgent` analysis submissions funnel
through, so a re-submitted question reuses the prior remote task instead of
launching a duplicate. A fingerprint hit is verified against the live remote
status before reuse: an in-flight or succeeded task is reused, while a failed
or cancelled task is written back and resubmitted.

## Response Envelope

Every MCP tool and HTTP API response is shaped as a two-block envelope:

- `formatted`: the normalized display view with `answer`,
  `follow_up_questions`, `metadata`, `references`, plus `tabular` for
  DataAgent (`{"headers": [...], "rows": [...]}`) and `output_dirs`
  for DigitalDesign fan-out paths.
- `raw`: the sanitized handler payload preserving provider-returned
  `choices[].message.reasoning_content`, `usage`, `tool_calls`,
  `system_fingerprint`, forward-compatible extensions, and a
  `phytomni_state` namespace carrying the agent's LangGraph
  intermediate state (`retrieved_docs`, `gene_id`, `rewrite_query`,
  `research_dimensions`, `plan`, `tool_usages`, ...).

**Default mode**: MCP stdio responses include only the `formatted`
block; the `raw` block is omitted to reduce response volume. Set
`PHYTOMNI_DEBUG=1` (or pass the HTTP per-request `debug` flag) to
include `raw` in every response.

Credential-pattern keys are stripped recursively before `raw` reaches the
wire. See [MCP Tool Reference](docs/reference/mcp-tools.md) for the per-tool
formatted view and [HTTP API](docs/reference/http-api.md) for the full envelope
contract on `/v1/chat/completions` and `/v1/agents/{agent}/runs`.

## Architecture

The MCP entrypoint is `src/mcp_server_phytomni/server.py`. The public MCP
surface lives in `src/mcp_server_phytomni/mcp/`, domain implementations live
under `src/mcp_server_phytomni/agents/<domain>/`, and shared runtime,
storage, auth, configuration, and cache helpers live in `runtime/`,
`storage/`, `auth/`, `config/`, `common/`, and `func_cache/`.

For the full package map, dispatch boundary, LangGraph wrapper policy,
configuration ownership, and cache policy, see
[Architecture](docs/explanation/architecture.md).

## Configuration

Local plaintext `src/mcp_server_phytomni/config/.env` takes precedence at
startup; trusted-customer images fall through to an encrypted
`.env.encrypted` envelope plus a runtime license key
(`PHYTOMNI_LICENSE_KEY` env var or `config/.license_key` file). The
project never commits plaintext `.env` files, API keys, OBS credentials,
generated cache databases, or local virtual environments. `.dockerignore`
keeps plaintext `.env` out of customer images so the encrypted envelope
remains the only effective source inside images.

See [Configuration](docs/reference/configuration.md) for required variables,
encrypted envelope behavior, HTTP API settings, cache paths, and e2e
tunables. See [Deployment and Storage](docs/guides/deployment.md) for
customer-image distribution, OBSFS-first storage, scratch path layout, and
startup troubleshooting.

### Customer Relay Mode

A relay-mode child Bot (`PHYTOMNI_RELAY_MODE=1`) holds no operator
endpoints or secrets: it routes every model, retrieval, NL2SQL, BI,
analysis, task, and OBS call through an operator-hosted relay
(`/v1/relay/*`) authenticated with a single `ptm_` key, and roots its OBS
object paths under the operator-assigned `RELAY_USER_ID` tenant namespace
(the operator's OBS relay confines each key to its own namespace). Copy
[`config/.env.customer.example`](src/mcp_server_phytomni/config/.env.customer.example)
to `.env` and fill in only the `PHYTOMNI_RELAY_*` values. The operator
side is documented in [HTTP API](docs/reference/http-api.md) *Relay* and the
[runbook](docs/ops/http-api-runbook.md) *Relay Operations*.

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

Render registered LangGraph agents as Mermaid for visualization:

```bash
python scripts/visualize_agent_graphs.py --list
python scripts/visualize_agent_graphs.py --agent brief_gene
```

The gate covers secret scanning, compile checks, whitespace, Python
format/lint/type checks, shell/YAML/JSON/Markdown/TOML checks,
`demo_data/` idempotency, and offline pytest. See
[Development](docs/guides/development.md) for the full command matrix, CI scope,
dependency policy, config normalization, and common troubleshooting.

## Documentation

- [Architecture](docs/explanation/architecture.md): package layout, MCP dispatch,
  LangGraph wrappers, configuration ownership, and cache policy.
- [Agent Graphs](docs/explanation/agent-graphs.md): subgraph registry, schema-mismatch
  adapter, graph manifest export, and the visualization command.
- [MCP Tool Reference](docs/reference/mcp-tools.md): public tool arguments,
  sync/async behavior, status polling, and demo payload links.
- [HTTP API](docs/reference/http-api.md): service startup, per-user keys,
  endpoints, polling, retention, and response shape.
- [CLI Reference](docs/reference/cli.md): `phytomni`, `phytomni-api`,
  `phytomni-api-key`, and `phytomni-cache`.
- [Configuration](docs/reference/configuration.md): local `.env`, encrypted
  envelopes, API variables, cache paths, and live e2e tunables.
- [Deployment and Storage](docs/guides/deployment.md): encrypted customer
  configuration, OBSFS fallback behavior, and scratch directory policy.
- [Development](docs/guides/development.md): local gates, CI, dependency policy,
  demo fixtures, E2E commands, and troubleshooting.
- [STYLE.md](STYLE.md): naming, docstrings, imports, compatibility rules,
  and repository-specific code style.
- [CHANGELOG.md](CHANGELOG.md): dated release history (0.1.0–0.1.3).
- [CONTRIBUTING.md](CONTRIBUTING.md): setup, local gate, test markers,
  commit convention, and dependency policy.
- [SECURITY.md](SECURITY.md): supported versions, vulnerability
  reporting, and the secret-handling posture.
- [docs/](docs/README.md): the full documentation map.

## License

This project is licensed under the terms specified in [LICENSE](LICENSE).

## Authors

- Shang Xie <xieshang0608@gmail.com>
- Yichao Mao <maoyc_0316@163.com>
- Hu Li <lihu0628@qq.com>
- Xiaofeng Gu <guxiaofeng@caas.cn>

Copyright (c) Biotechnology Research Institute, Chinese Academy of
Agricultural Sciences. 2024-2026. All rights reserved.
