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

## Setup

1. Configure the server environment by copying the example `.env` and
   filling in real credentials:

   ```bash
   cp src/mcp_server_phytomni/config/.env.example \
      src/mcp_server_phytomni/config/.env
   # edit .env with your API keys, OBS credentials, etc.
   ```

2. Install the optional `demo` extra so the regenerator (and any tests
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

### Async polling tunables

Tools that return task handles (`AnalystAgent`, `DeepGenomeAgent`,
`InSilicoResearchAgent`, `DigitalDesignAgent`, `GeneNetworkAgent`)
poll to terminal state via `helpers/polling.py`. Override the default
ten-minute per-task timeout with:

```bash
PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS=1200 \
    uv run pytest e2e/test_analyst_agent_e2e.py -v
```

### HTTP API e2e

`test_api_http_e2e.py` is the one file that does NOT go through the
stdio MCP client. It boots `phytomni-api` as a real uvicorn subprocess
on an ephemeral port, mints a per-user key in a throwaway SQLite store
via `ApiKeyStore`, then drives `POST /v1/chat/completions` over real
HTTP for all four OpenAI-compatible models. Review and BriefGene each
block the synchronous endpoint ~10 min, so the file runs ~20 min and
all four model calls run by default (no opt-in flag):

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_api_http_e2e.py -v
```

To validate just the subprocess/key/HTTP harness without paying the
~20 min, restrict to the cheap smoke/negative cases:

```bash
PHYTOMNI_RUN_INTEGRATION=1 PHYTOMNI_ALLOW_NETWORK=1 \
    uv run pytest e2e/test_api_http_e2e.py -v \
    -k "healthz or models or auth or unknown or stream or obs"
```

Tunables: `PHYTOMNI_E2E_API_STARTUP_SECONDS` (health-gate budget,
default 120) and `PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS` (per-request
read timeout, default 1200).

## Layout

```
e2e/
├── README.md
├── pyproject.toml          # local pytest rootdir config
├── conftest.py             # session client + OBS publish fixtures
├── helpers/
│   ├── client.py           # PhytomniMcpClient context manager
│   ├── obs_publish.py      # per-session demo_data upload
│   └── polling.py          # async task polling
├── test_*_e2e.py           # one file per public MCP tool
└── test_api_http_e2e.py    # live HTTP API (chat surface)
```
