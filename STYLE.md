# Phytomni-Bot Style Guide

This guide defines repository style for code, tests, and documentation. It is
intentionally conservative: public MCP tool names, JSON schemas, environment
aliases, and OBS path conventions stay stable unless a change is explicitly
planned and tested.

## Naming

- Python modules and files use `snake_case.py`.
- Agent implementation modules keep the `*_agents.py` suffix.
- Test files use `test_*.py`; shared pytest helpers live in `conftest.py`.
- Classes, Pydantic models, and exceptions use `PascalCase`.
- Exception classes that represent errors end with `Error`.
- Functions, methods, fixtures, and variables use `snake_case`.
- Constants and enum member names use `UPPER_SNAKE_CASE`.
- Enum values that are part of the MCP API remain backward compatible.
- Environment variable names use `UPPER_SNAKE_CASE`. Existing public names may
  be preserved through Pydantic aliases during migration.
- Avoid unclear abbreviations in new code. Prefer names such as
  `knowledge_config` over `kc` when the value crosses function boundaries.

## Public API Compatibility

- Do not rename public MCP tool names without a compatibility alias and tests.
- Do not change public wrapper signatures unless the migration plan explicitly
  calls for it.
- Keep request and response schemas JSON-schema friendly.
- Internal state keys may be renamed only with focused tests around the owning
  agent workflow.

## Docstrings

- Public modules, classes, functions, and methods use Google-style docstrings.
- Short private helpers may omit docstrings when their name and type hints are
  enough.
- A public docstring should explain behavior, important side effects, and
  compatibility constraints.
- Use `Args:`, `Returns:`, and `Raises:` sections when they add useful
  information. Do not repeat obvious type hints.
- Never include secrets, tokens, private endpoints, or real credentials in
  examples.

## Copyright Header

Every Python source and test file should start with this header:

```python
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
```

Place the module docstring immediately after the copyright and author lines.
Author names use `name (email)` on the first line and aligned continuation
lines for additional authors.
README-style prose may wrap the same copyright text across lines.

## Imports

- Group imports into exactly three top-level sections: Python standard library
  modules, third-party packages, then local package imports.
- Separate import sections with one blank line.
- Sort imports alphabetically inside each section. Let Ruff's `I` rules keep
  this mechanical ordering consistent.
- Keep imports at module top level unless a local import is needed to avoid a
  real optional dependency, circular import, or expensive startup side effect.
- Prefer explicit imports over broad module imports when it improves type
  checking and readability.

## Formatting

- Use Python 3.12 syntax.
- Keep the configured line length at 79.
- Let `black`, `ruff`, `flake8`, `mypy`, `pyright`, and `pylint` define the
  automated baseline.
- Ruff's `N` rules are enabled to enforce PEP 8 naming conventions.
- New lint disables must be narrow, documented, and treated as temporary unless
  the rule is intentionally incompatible with public API stability.

## Tests

- Default pytest tests must run offline and without real secrets.
- Mark real external-service tests with `integration` or `network`.
- Add focused tests when renaming internal keys, moving wrappers, changing
  cache keys, or changing LangGraph execution flow.
