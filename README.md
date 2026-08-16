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
editable packages and pip-only extras (including the explicit MarkItDown
document-converter extras and Python 3.14-compatible YouTube client) are
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
phytomni call ChatAgent \
  '{"user_query": "Explain C3 photosynthesis.", "obs_file_list": []}'
```

`phytomni call` prints `formatted.answer` first, then optional tabular /
references / task-metadata blocks. DeepGenome and other background
submit tools need a long-lived `phytomni-api` or MCP server session —
a one-shot `phytomni call` exits the server subprocess and cancels
in-process background work. Details: [CLI Reference](docs/reference/cli.md).

For asynchronous HTTP runs, set `PHYTOMNI_API_URL` and
`PHYTOMNI_API_KEY`, then use the report-safe `submit`, `status`, and `follow`
commands. `follow` prints the final or latest intermediate Markdown to stdout
and progress to stderr:

```bash
phytomni --api-url http://127.0.0.1:8080 follow run-1
```

DeepGenome has a local, revisioned report lifecycle. An atomic reservation
creates the owner run, umbrella task, required BriefGene section, and child
tracking rows before the in-process coordinator begins. BriefGene must succeed
before any remote analysis is submitted; optional children may fail or remain
in flight without hiding the latest `intermediate_report`. The coordinator
owns bounded polling and writes `report_revision`; HTTP status, MCP
`GetTaskStatus`, and the HTTP-backed CLI read that local snapshot. A service
restart does not resume after process restart: the orphan is settled at the
documented failure boundary, with its last intermediate report preserved.
This release does not ship a cross-process durable worker for DeepGenome,
does not expose DataAgent over HTTP streaming, and has no production/live
integration acceptance. Web/Go, DBA/Ops, live-backend, and production rollout
are separate owner checks; local Bot gates do not close those obligations. The
public [DeepGenome contract fixtures](docs/contracts/deep-genome/README.md)
show the sanitized report states and are shape examples only, not live
acceptance evidence.

The HTTP API runs as a separate process:

```bash
phytomni-api
phytomni-api-key create --user-id alice --name laptop
```

See [CLI Reference](docs/reference/cli.md) for the installed commands and
[HTTP API](docs/reference/http-api.md) for authentication, endpoint contracts,
run polling, retention, OpenAI-compatible chat, human-in-the-loop review
resume, and A2UI confirm/form/choice widgets
(Surface Author + Chat/Review N=2). Copyable
A2UI goldens for Web/Go consumers (`chat_confirm`, `review_confirm`,
`chat_form`, `chat_choice`, `review_form`, `review_choice`,
`multi_turn`) live under
[docs/contracts/a2ui/](docs/contracts/a2ui/README.md).

The current-SHA Bot acceptance procedure, focused packet, gate
interpretation, and external-acceptance boundary live in the [Bot contract
acceptance runbook](docs/ops/bot-contract-acceptance-runbook.md). The
[convergence ledger](docs/ops/bot-contract-convergence-ledger.md) and
[compatibility register](docs/ops/bot-compatibility-register.md) record
dispositions; local Bot evidence does not claim Web/Go or staging acceptance.

The sanitized unified managed-attachment contract is pinned by five scenarios
in [the attachment fixture packet](docs/contracts/unified-attachments/).
Its JSON, channel projections, and ordered Expert eligibility are shape
evidence only; they do not establish browser, backend, staging, or production
acceptance.

### Locale And File Attachments

HTTP agent requests accept `locale` at the top level. The precedence is
explicit body value, the first supported `Accept-Language` item, then
inference from the latest user query (`zh-CN` for Han characters, otherwise
`en-US`). Header values `en` / `en-*` normalize to `en-US`, and `zh` /
`zh-*` normalize to `zh-CN`. A body value outside `en-US` / `zh-CN` returns
`422 unsupported_locale`. Locale affects generated natural-language text and
fixed error messages only; it is never an authorization or tool-selection
input. Paused runs keep their stored locale when resumed.

Example native run:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/agents/chat/runs \
  -H "Authorization: Bearer ptm_..." \
  -H 'Content-Type: application/json' \
  -d '{
    "arguments": {"user_query": "What is this rice gene?", "obs_file_list": []},
    "locale": "en-US"
  }'
```

Use the resumable `POST /v1/files` control route to create an asset, upload
parts with the returned capability, and complete it. Pass the completed
`asset_id` in an HTTP agent request's `attachments` list; Bot resolves it
owner-scoped before invoking the selected agent. Managed assets are projected
solely from their persisted class and the selected Agent's final channel shape:
document-only Agents receive managed references in `obs_file_list`, while
dual-channel Agents receive dataset-class assets in `data_list` and the rest
in `obs_file_list`. Managed projection has no pre-invocation filename suffix,
CSV, MIME, purpose, or description gate; managed `data_list` values are exact
empty strings. Capability discovery describes only managed channel presence,
the native argument name, and invocation limits; it does not advertise
general managed support for extensions, formats, encoding, delimiters,
compression, or descriptions. Legacy raw native inputs are separate:
document references keep their documented purpose and extension checks, and
legacy `data_list` entries remain CSV/purpose-validated with nonblank
descriptions. The transfer limit is
10 GiB by default. Agent invocation remains bounded to 10 attachments,
26,214,400 bytes per attachment, and 52,428,800 bytes in total. Repeated
asset ids, foreign owners, incomplete assets, unsupported channels, and
metadata mismatches fail closed. Existing preconfigured OBS paths in
`data_list` are a separate legacy policy and are not user-upload registration
evidence. See the
[HTTP attachment
contract](docs/reference/http-api.md#attachment-invocation-contract)
and [operator
runbook](docs/ops/http-api-runbook.md#attachment-preflight-and-orphan-review)
for the capability matrix, stable error codes, and orphan review boundary.

Research input resolution is a separate exact-key contract layered on the
existing Research run surface. It accepts only the three documented `data:` /
configured-bucket grammars, preserves query spans, resolves managed assets
owner-scoped, and resolves pasted dataset metadata without list or body access.
The request-wide defaults are 64 managed references, 64 pasted references,
and 128 combined references (each hard-capped at 256); the effective values
are advertised only by the versioned `research_input_resolution_v1` descriptor
when direct or relay readiness is proven. Idempotent admission, four public
stages, cancellation, bounded failure projections, and relay grants remain
separate from the unchanged `relay:obs` and MCP contracts. Copyable sanitized
fixtures and the operator boundary are in the
[Research input-resolution contract packet](docs/contracts/research-input-resolution/README.md).

HTTP streaming has two explicit failure boundaries. The API eagerly prepares
the tool and primes the first AG-UI event before committing SSE response
headers; setup or priming failures are ordinary JSON errors and settle a
pre-created run as `failed`. Once the first event is primed, an ordinary
producer failure emits exactly one sanitized `RunError`, does not emit
`RunFinished`, and closes with one `[DONE]`. Client cancellation propagates
for cleanup: disconnecting before `RunFinished` settles `failed` without a
synthetic frame, while disconnecting after `RunFinished` preserves success.
See [SSE Streaming](docs/reference/http-api.md#sse-streaming) for the
redaction and operator-smoke contract.

### Capability Boundary in 0.1.3

The Bot-side implementation for the 14 dependency-underutilization items is
complete on the `0.1.3` release branch: six gate rules, four reliability
improvements, three dormant-asset connections, and MCP stdio progress are
shipped. Graph progress is available on HTTP SSE and, for Knowledge / Review /
Data / BriefGene, through MCP `progressToken`. Review interrupt/resume uses a
persistent local SQLite checkpointer. A2UI Chat/Review widgets are always
on. Web/Go
integration, DBA/Ops evidence, live backend acceptance, and production rollout
remain separately owned checks and are not closed by this Bot-local statement.

The A2A server core is now available as an opt-in 0.1.3 surface. It remains
disabled unless `PHYTOMNI_A2A_ENABLED=1` and a public base URL are configured;
flag-off behavior is unchanged. Phase 2 supports the public Agent Card,
authenticated JSON-RPC `SendMessage`, `SendStreamingMessage` over SSE, and
owner-scoped `GetTask` polling. Review and A2UI-backed Chat pauses expose a
bounded `INPUT_REQUIRED` data artifact for the next resume phase. Cancellation,
and same-task `SendMessage` resume are available for that pause; A2A
`CancelTask`, push notifications, and extended cards remain later-phase work.

Phase 3/4 also ship outbound MCP/A2A discovery and opt-in Research/Design
delegation behind `PHYTOMNI_INTEROP_ENABLED=1`. Operators provide a target
registry and separate credential references; the read-only
`GET /v1/interop/capabilities` endpoint requires the `agents` scope and
returns sanitized capability metadata. Research and Design requests opt in
per call with `interop_mode="auto"` or `interop_mode="required"` plus
operator-registered `interop_targets`; the default `"off"` mode never
discovers or invokes a peer. `auto` records a degraded local fallback when no
external evidence is available, while `required` fails closed. Enabling or
changing the registry requires an API process restart; the flag is off by
default.

- **Capability:** A2A Agent Card and `/a2a` server
  **0.1.3 status:** Opt-in core

- **Capability:** Calls to external MCP tools or A2A agents
  **0.1.3 status:** Explicit opt-in from Research/Design

- **Capability:** User-scoped memory CRUD API
  **0.1.3 status:** Opt-in; bounded read-only recall

Internal `phyto.progress.phase` values are stage labels, not A2A task states.
The A2A status adapter maps task lifecycle state independently. Outbound
delegation and explicit memory remain inactive by default in this release.
When memory is enabled, its agents receive bounded read-only recall from the
authenticated user's namespace; only the explicit CRUD API writes memory.
There is no autonomous `langmem` writer and no embedding or semantic index.

## Available MCP Tools

For tools that include `obs_file_list`, pass an empty list (`[]`) when no
document upload is needed. See [MCP Tool Reference](docs/reference/mcp-tools.md)
for detailed argument semantics, async behavior, and demo payload links.
To attach a document through HTTP, create and complete a resumable asset with
`POST /v1/files`, then put the returned `asset_id` in the request's
`attachments` list. Bot converts the owner-checked asset to the internal
`obs_file_list` shape before the agent runs. Demo upload samples live under
[`demo_data/`](demo_data/). Details:
[MCP Tool Reference — Uploading
documents](docs/reference/mcp-tools.md#uploading-documents-for-obs_file_list).
New upload creates require the explicit server-classified `purpose` value
`dataset` or `document`; historical `chat_attachment` rows remain readable as
documents but are not writable through this route. Completion responses expose
only the safe asset descriptor and never return `purpose` or provider data.

- **Tool:** `ChatAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Purpose:** General plant science chat with optional document context.

- **Tool:** `KnowledgeAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Purpose:** Literature retrieval and RAG-based synthesis.

- **Tool:** `DataAgent`
  **Kind:** sync
  **Required arguments:** `user_query`
  **Purpose:** Natural-language SQL query rewriting and database search.

- **Tool:** `ReviewAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Purpose:** Multi-step literature review and deep research generation.

- **Tool:** `BriefGeneAgent`
  **Kind:** sync
  **Required arguments:** `user_query`
  **Purpose:** Rich gene preamble (introduction + Gene Profiles with Basic
  Genomic Information and four analytical sections) from BI
  annotations and
  literature.

- **Tool:** `AnalystAgent`
  **Kind:** async
  **Required arguments:** `goal_description`, `data_list`, `obs_file_list`
  **Purpose:** Bioinformatics workflow retrieval, planning, submission, and
  status handling.

- **Tool:** `DeepGenomeAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `gene_id`
  **Purpose:** Multi-omics gene function analysis.

- **Tool:** `InSilicoResearchAgent`
  **Kind:** async
  **Required arguments:** `user_query`, `data_list`, `obs_file_list`
  **Purpose:** Decompose papers or research goals into computational tasks.

- **Tool:** `DigitalDesignAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `gene_id`, `obs_file_list`
  **Purpose:** Protein and promoter design workflows.

- **Tool:** `GeneNetworkAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `to_id`, `obs_file_list`
  **Purpose:** Gene network analysis for species and trait ontology IDs.

- **Tool:** `GetTaskStatus`
  **Kind:** sync
  **Required arguments:** `task_id`
  **Purpose:** Non-blocking status lookup for a previously submitted async task.

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

Static-analysis exemptions use a default-deny registry. Temporary records are
migration-only and must converge to zero; apply the non-degradation, no-waiver
test before retaining any directive, and keep only evidence-backed structural
compatibility boundaries under review. See [STYLE.md](STYLE.md#lint-waivers)
for the approval and remediation contract.

## Documentation

- [Architecture](docs/explanation/architecture.md): package layout, MCP
  dispatch,
  LangGraph wrappers, configuration ownership, and cache policy.
- [Agent Graphs](docs/explanation/agent-graphs.md): subgraph registry,
  schema-mismatch
  adapter, graph manifest export, and the visualization command.
- [MCP Tool Reference](docs/reference/mcp-tools.md): public tool arguments,
  sync/async behavior, status polling, and demo payload links.
- [HTTP API](docs/reference/http-api.md): service startup, per-user keys,
  endpoints, polling, retention, and response shape.
- [Bot contract acceptance](docs/ops/bot-contract-acceptance-runbook.md):
  current-SHA focused packet, evidence hashes, gate interpretation, and
  external acceptance boundary.
- [Bot convergence ledger](docs/ops/bot-contract-convergence-ledger.md):
  requirement evidence, five handoff dispositions, and rollback ownership.
- [Bot compatibility register](docs/ops/bot-compatibility-register.md):
  migration bridges that remain gated until paired consumer evidence exists.
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
