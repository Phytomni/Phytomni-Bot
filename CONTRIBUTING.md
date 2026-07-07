# Contributing to Phytomni-Bot

Thanks for your interest in contributing. This guide covers the setup,
local validation gate, test markers, commit convention, and dependency
policy for working in this repository.

## Setup

Using `uv`:

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev,demo]"
```

The `[demo]` extra pulls `reportlab` and `openpyxl`, which the local
gate needs to regenerate `demo_data/` before running the test suite.

Using conda or mamba instead:

```bash
conda env create -f environment.yml
conda activate phytomni-bot
pip install -e ".[dev,demo]"
```

Python 3.12, 3.13, and 3.14 are all supported; 3.12 is the default
local example. Copy `src/mcp_server_phytomni/config/.env.example` to
`src/mcp_server_phytomni/config/.env` for local runs — never commit
real secrets.

## The Local Gate

Run the full gate before pushing (the pre-push hook runs the same
script, so passing it locally matches CI):

```bash
./scripts/validate_local.sh
```

For faster feedback while iterating, run the scoped gate over just the
active change region:

```bash
make scoped
```

See [docs/guides/development.md](docs/guides/development.md) for the
full command matrix, CI scope, config normalization, and
troubleshooting notes — this file intentionally does not duplicate it.

## Tests

```bash
uv run pytest
```

Default `pytest` runs offline tests only. Tests are grouped by marker:

- `unit` — fast offline unit tests.
- `server` — MCP server schema and dispatch tests.
- `agent` — offline agent smoke tests with mocked external services.
- `integration` — configured external-service tests; run with
  `PHYTOMNI_RUN_INTEGRATION=1`.
- `network` — real network, OBS, or LLM calls; run with
  `PHYTOMNI_ALLOW_NETWORK=1`.

For behavior changes, add or update focused tests covering the change.

## Commit Convention

Subject line: `emoji + Word: summary` (e.g. `🐛 Fix:`, `✨ Add:`,
`♻️ Reorg:`, `📝 Docs:`, `🧪 Tests:`). Body is bullets-only with a
blank line between bullets; the first bullet states the problem.
Commit subjects and bodies are English-only.

## Dependency Policy

Runtime and development dependencies are declared in `pyproject.toml`
as lower bounds (`>=`). `[project.optional-dependencies]` is the
single source of truth for the `dev` and `demo` extras. Do not commit
`uv.lock`; it may exist locally but stays ignored.

## Further Reading

- [STYLE.md](STYLE.md): naming, docstrings, imports, and
  repository-specific code style.
- [CHANGELOG.md](CHANGELOG.md): dated release history.
- [SECURITY.md](SECURITY.md): supported versions and how to report a
  vulnerability.
