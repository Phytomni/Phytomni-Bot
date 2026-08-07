# Phytomni-Bot Live Business E2E Suite

This directory holds the live business-layer end-to-end tests that
exercise every MCP tool through real `PhytomniMcpClient` stdio calls
against fully configured external backends (LLM, OBS, NL2SQL service,
analysis platform, etc.). It is the project's "highlight" suite: clone
the repo, fill in `.env`, run one command, and watch every agent
respond with realistic output using the small fixtures committed under
[`../demo_data/`](../demo_data/).

## Why it lives outside `tests/`

The root [`tests/conftest.py`](../tests/conftest.py) installs an
autouse `block_external_http` fixture that raises on any unmarked HTTP,
and the root `[tool.pytest.ini_options]` filters out
`integration`/`network`-marked tests by default. The live suite needs
neither of those constraints — it intentionally calls real services
every run — so it sits in its own directory with its own
[`pyproject.toml`](pyproject.toml) so `pytest e2e/` uses this directory
as the pytest rootdir and skips the root config entirely.

Default `uv run pytest` (the gate, CI) is unaffected: the root
`testpaths = ["tests"]` keeps it from discovering anything here.

## Entry policy

Every test enters through `PhytomniMcpClient.call_tool(...)`, the
public stdio client surface
([`src/mcp_client_phytomni/client.py`](../src/mcp_client_phytomni/client.py)).
That is the user-facing path and the one this suite proves works.

If the client cannot start the server subprocess, the suite fails fast
rather than falling back to handler-direct dispatch or wrapper-direct
calls — those layers are already covered by
[`../tests/server/`](../tests/server/) and
[`../tests/agents/`](../tests/agents/) respectively, and the failure
itself is the bug worth surfacing.

## Response shape

`PhytomniMcpClient.call_tool(...)` returns a `FormattedToolResult`
deserialised from the server's `{formatted, raw}` envelope. The
client-path tests bind to `response.formatted.answer` (per-agent
validators in [`helpers/assertions.py`](helpers/assertions.py)),
`response.formatted.tabular` for DataAgent, and
`response.formatted.output_dirs` for DigitalDesign fan-out.

The HTTP-path smokes ([`test_api_http_e2e.py`](test_api_http_e2e.py)
and [`test_concurrent_http_e2e.py`](test_concurrent_http_e2e.py))
parse the same envelope from the wire:

- Chat completions: `body["choices"][0]["message"]["content"]` for the
  OpenAI canonical view, `body["formatted"][...]` for the typed
  display view, `body["raw"][...]` for provider-returned
  `reasoning_content` / `usage` / forward-compatible extensions.
- Native agent runs: `body["result"]["formatted"][...]` for the
  display view, `body["result"]["raw"][...]` for the same
  raw block.

The submit response is a submission acknowledgement, not a completed report.
Use `GetTaskStatus` or `GET /v1/runs/{run_id}` for one non-blocking lookup. A
succeeded analyst-class task exposes `final_report`; Design and Network also
expose their real artifact/output paths. Offline mocks validate the shape; this
does not prove live backend acceptance.

See [`../docs/reference/http-api.md`](../docs/reference/http-api.md) for the
full
envelope contract and
[`../docs/reference/mcp-tools.md`](../docs/reference/mcp-tools.md)
for the per-tool formatted view.

### Citation database lifecycle

The live suite preserves an explicit operator citation database when either
`CITATION_DB_PATH` or `PHYTOMNI_CITATION_DB_PATH` is already configured. When
neither alias is set, the session fixture builds one empty, valid schema-v1
SQLite artifact in its temporary session directory only to satisfy real MCP
and HTTP subprocess startup validation. It does not contain bibliographic
records and does not prove citation enrichment.

Use an operator-built artifact to exercise bibliographic enrichment. E2E runs
without that artifact prove subprocess startup and the rest of the configured
business workflow only; they do not prove bibliographic enrichment.

## Setup

1. Configure the server environment by copying the example `.env` and
   filling in real credentials:

   ```bash
   cp src/mcp_server_phytomni/config/.env.example \
      src/mcp_server_phytomni/config/.env
   # edit .env with your API keys, OBS credentials, etc.
   ```

1. Install the optional `demo` extra so the regenerator (and any tests
   that touch the bundled fixtures) can import `openpyxl` and
   `reportlab`:

   ```bash
   uv pip install -e ".[dev,demo]"
   ```

## Run

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/ -v
```

The two environment flags mirror the gates set by the root conftest:
even though `e2e/` does not inherit those checks, keeping them in the
invocation is a useful reminder that this command will make real
network and platform calls.

### Reasoning/content normalization evidence

The offline and live evidence helper
[`../scripts/capture_reasoning_normalize.py`](../scripts/capture_reasoning_normalize.py)
records compact before/after JSONL summaries for the narrow provider
fault where a closed `<think>...</think>` block carries the final answer
tail in `reasoning_content` or at the front of `content`.

Fixture mode is deterministic and does not call the network:

```bash
uv run python scripts/capture_reasoning_normalize.py --fixtures
```

Live mode calls the configured provider repeatedly and writes the same
summary shape under `e2e/output/`. A live run only proves that the
normalizer corrected the intermittent fault when a record has
`repaired=true`, a before-state with the answer tail in a tagged field,
and matching MCP/API after-state content. If no live record triggers
repair, the anomaly was not observed in that sample; it is not evidence
that the upstream issue disappeared.

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run python scripts/capture_reasoning_normalize.py --live --runs 30
```

### Async polling tunables

Tools that return task handles (`AnalystAgent`, `DeepGenomeAgent`,
`InSilicoResearchAgent`, `DigitalDesignAgent`, `GeneNetworkAgent`)
poll to terminal state via `helpers/polling.py`. Override the default
ten-minute per-task timeout with:

```bash
PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS=1200 \
    uv run pytest e2e/test_analyst_agent_e2e.py -v
```

Additional knobs read by the polling path:

- `PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS` — read timeout budget for the
  initial async-tool submit call (before polling begins; resolved in
  `helpers/client.py`).
- `PHYTOMNI_E2E_TASKS_DB` — path to the `server_tasks.db` that
  `helpers/polling.py` reads, for when the MCP server runs from a
  non-default working directory.
- `PHYTOMNI_E2E_RUN_KA_UPLOAD` — set to `1` to also run the
  KnowledgeAgent uploaded-document variant; skipped by default because
  the retrieve→rerank→LLM fan-out with an attached document is
  backend-bound and can exceed 20-30 min on a degraded tier.

### HTTP API e2e

`test_api_http_e2e.py` and `test_concurrent_http_e2e.py` are the two
files that drive the API over real HTTP instead of the stdio MCP
client. Both boot `phytomni-api` as a uvicorn subprocess and mint a
per-session key through the shared `helpers/api_server.py` helper
(`boot_phytomni_api`, which also runs the health-gate); see "Layout"
below. `test_api_http_e2e.py` then drives every cutover-relevant HTTP
route over real HTTP. Review and BriefGene each block the synchronous
endpoint ~10 min, so the file runs ~20 min and all four model calls run
by default (no opt-in flag):

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_api_http_e2e.py -v
```

The Web cutover smoke set covers, beyond the four chat completions:

- `test_api_keys_service_token_lifecycle` — POST mint user key → GET
  list → DELETE revoke under the service-token principal (mirrors
  the Web ops 90-day rotation workflow).
- `test_chat_stream_sse_returns_data_lines_and_done` — `stream=true`
  on `phyto-chat` returns `text/event-stream` with `data: {...}\n\n`
  frames and a terminal `data: [DONE]`.
- `test_stream_true_rejected_for_non_chat_models` — per-model matrix
  pinning the post-Phase-5 policy (chat 200 SSE, other 3 → 400).
- `test_runs_history_self_query_by_dialogue_id` — chat with
  `dialogue_id` persists into `runs` and surfaces via
  `GET /v1/runs?dialogue_id=`.
- `test_runs_history_delegated_user_id_via_service_token` — service
  token can read another user's runs (ops-debug surface).
- `test_files_upload_returns_obs_path` — `POST /v1/files` multipart
  returns an `agent_data/uploads/…` OBS path.

The boot helper sets `PHYTOMNI_API_SERVICE_TOKEN` to a fixture-known
value so admin routes are exercisable without a second uvicorn boot.

To validate just the subprocess/key/HTTP harness without paying the
~20 min agent calls, restrict to the cheap smoke/negative cases:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_api_http_e2e.py -v \
    -k "healthz or models or auth or unknown or stream or obs \
        or api_keys or runs_history or files_upload"
```

Tunables: `PHYTOMNI_E2E_API_STARTUP_SECONDS` (health-gate budget,
default 120) and `PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS` (per-request
read timeout, default 1200).

### Result archive delivery e2e

The terminal archive suite submits Analyst, Research, Network, and Design over
their native HTTP routes, then verifies the authenticated Bot-side ZIP contains
`summary.md` plus at least one admitted scientific result. The retry suite boots
the guarded `helpers/fault_injected_api.py` module, exhausts exactly three
automatic publication attempts, and proves manual retry reuses the same child
tasks and inventory digest:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_remote_run_terminal_payload_e2e.py \
    e2e/test_result_delivery_retry_e2e.py -v
```

The fault-injected module refuses to start unless integration is explicitly
enabled, the failure count is exactly three, and its task database resolves
below `/tmp`.

### Rerank isolation probe

`test_rerank_probe_e2e.py` isolates the rerank hop of the
`retrieve → rerank → LLM` chain so a gateway failure (e.g. 502) can be
pinned on rerank rather than retrieve. It is a deliberate one-layer
drop from the "enter through `PhytomniMcpClient`" policy (like
`helpers/polling.py` reading `server_tasks.db` directly): it imports
`rerank` and feeds it a committed, desensitized pre-rerank `doc_list`
captured from one real retrieve response
([`fixtures/rerank_seed_docs.json`](fixtures/rerank_seed_docs.json)),
so the retrieve hop is replaced by the fixture and only rerank makes a
live call. It runs in ~1 s:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_rerank_probe_e2e.py -v
```

The fixture keeps only the fields `_rerank_docs` consumes (`chunk_id` /
`title` / `big_content` || `content`); deployment identifiers are
stripped, and one doc carries `big_content` so the probe also covers
the `big_content` precedence branch.

## Layout

```text
e2e/
├── README.md
├── pyproject.toml          # local pytest rootdir config
├── conftest.py                  # session client + OBS publish fixtures
├── fixtures/                       # committed seed payloads for probes
│   └── rerank_seed_docs.json    # desensitized pre-rerank doc_list
├── helpers/
│   ├── api_server.py            # shared phytomni-api uvicorn boot
│   ├── assertions.py            # shared keyword/markdown assertions
│   ├── client.py                # PhytomniMcpClient context manager
│   ├── obs_publish.py           # per-session demo_data upload
│   └── polling.py               # async task polling
├── test_*_e2e.py                # one file per public MCP tool
├── test_rerank_probe_e2e.py       # rerank hop isolation (fixture-fed)
├── test_concurrent_client_e2e.py  # five chat-like agents over MCP stdio
├── test_concurrent_http_e2e.py    # five chat-like agents over HTTP
└── test_api_http_e2e.py         # live HTTP API (chat surface)
```
