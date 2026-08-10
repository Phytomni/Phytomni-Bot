# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Root pytest conftest: install offline test env before imports run.

Pytest loads ``conftest.py`` files in directory order from the
rootdir down. Putting the test-env install in the repo-root
``conftest.py`` makes it execute before ``tests/conftest.py`` parses
its module-level imports, which in turn means ``tests/conftest.py``
can keep its ``from mcp_server_phytomni...`` imports at the top of
the file (no ``E402`` / ``C0413`` / ``noqa``).

Several source modules (``storage/uploads.py``,
``storage/downloads.py``, ``agents/shared/analysis_storage.py``,
``agents/deep_genome/{profile,agent,report}.py``,
``agents/data/nl2sql.py``, ``agents/brief_gene/core.py``, ...) call
``ServerConfig()`` (or a subclass) at module-import time. The
deployment-specific endpoints/UUIDs are required-via-env, so without
this bootstrap pytest collection would raise ``ValidationError``
before any fixture had a chance to run.

The live ``e2e/`` suite has its own ``pyproject.toml`` with a
``[tool.pytest.ini_options]`` block, which makes pytest treat
``e2e/`` as a separate rootdir; this root ``conftest.py`` is therefore
NOT loaded for ``pytest e2e/`` and cannot stomp on the operator's
real ``.env``.
"""

from __future__ import annotations

import os

_TEST_ENV = {
    "DOMAIN_NAME": "pytest-domain",
    "USER_NAME": "pytest-user",
    "USER_PASSWORD": "pytest-password",
    "ACCESS_KEY_ID": "pytest-access-key-id",
    "SECRET_ACCESS_KEY": "pytest-secret-access-key",
    "BASE_URL": "https://example.invalid/llm",
    "MODEL_ID": "pytest-model",
    "API_KEY": "pytest-api-key",
    "CODER_URL": "https://example.invalid/coder",
    "CODER_MODEL": "pytest-coder-model",
    "CODER_API_KEY": "pytest-coder-api-key",
    "EMBED_URL": "https://example.invalid/embed",
    "EMBED_MODEL": "pytest-embed-model",
    "EMBED_API_KEY": "pytest-embed-api-key",
    "OUTBOUND_LLM_CONCURRENCY": "0",
    "OUTBOUND_RETRIEVAL_CONCURRENCY": "0",
    "OUTBOUND_RERANK_CONCURRENCY": "0",
    "OUTBOUND_NL2SQL_CONCURRENCY": "0",
    "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY": "0",
    "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY": "0",
    "OUTBOUND_IAM_CONCURRENCY": "0",
    "OUTBOUND_SPA_FAQ_CONCURRENCY": "0",
    "OUTBOUND_BI_CONCURRENCY": "0",
    "OUTBOUND_OBS_CONCURRENCY": "0",
    "OUTBOUND_RELAY_CONTROL_CONCURRENCY": "0",
    "OUTBOUND_INTEROP_CONCURRENCY": "0",
    "OUTBOUND_POOL_WAIT_WARN_SECONDS": "1",
    "GAUSS_DSN": ("postgresql://u:p@db.invalid:8000/test?sslmode=require"),
    # Deployment-specific endpoints (Phase 14.3.1): empty defaults in
    # config/defaults.py force operators to set these per-deployment.
    # Tests use stable example.invalid hosts so a stray real network
    # call would fail closed instead of leaking to a public endpoint.
    "RETRIEVE_URL": "https://example.invalid/retrieve",
    "RERANK_URL": "https://example.invalid/rerank",
    "SPA_FAQ_URL": "https://example.invalid/repos/{repo_id}/faqs",
    # Deployment-specific UUIDs (Phase 14.3.2): empty defaults in
    # config/defaults.py force operators to set per-deployment. Tests
    # use opaque ``pytest-<name>-id`` strings so any accidental
    # cross-tenant id leak shows up clearly in logs / assertions.
    # REPO_ID_DICT ships as a JSON string env value; pydantic-settings
    # parses it into Dict[str, int] automatically.
    "REPO_ID": "pytest-repo-id",
    "REPO_ID_DICT": '{"pytest-repo-id": 128}',
    "WORKSPACE_ID": "pytest-workspace-id",
    "SUBJECT_ID": "pytest-subject-id",
    "DATA_REPO_ID": "pytest-data-repo-id",
    "TOOL_REPO_ID": "pytest-tool-repo-id",
    "PROTOCOL_REPO_ID": "pytest-protocol-repo-id",
    "SPA_REPO_ID": "pytest-spa-repo-id",
    # Embedded-UUID URLs (DATABASE_URL / ANALYSIS_URL): empty
    # defaults in config/defaults.py mean these env vars are required
    # in every deployment — operators stamp them per environment
    # rather than baking them into the wheel.
    "DATABASE_URL": "https://example.invalid/database",
    "ANALYSIS_URL": "https://example.invalid/analysis",
    # Cloud-platform endpoints + compute-tier app-id map: empty
    # defaults in config/defaults.py mean these env vars are required
    # in every deployment, so a customer image never bakes Huawei
    # IAM / OBS regional hosts or shared compute-tier UUIDs into
    # the wheel. APP_ID ships as a JSON string so a single env var
    # carries the full small/medium/large map.
    "TOKEN_URL": "https://example.invalid/iam/v3/auth/tokens",
    "OBS_SERVER": "https://example.invalid/obs",
    "APP_ID": (
        '{"small": "00000000-0000-0000-0000-000000000001",'
        ' "medium": "00000000-0000-0000-0000-000000000002",'
        ' "large": "00000000-0000-0000-0000-000000000003"}'
    ),
    # Declarative GraphLoader flag pinned off in tests so the loader's
    # gated construction path (raises when disabled) is reachable from
    # the flag-on tests via monkeypatch without bleeding into the
    # default-off offline runs.
    "PHYTOMNI_GRAPH_LOADER": "0",
}


def _install_test_environment() -> None:
    """Install pytest-only env vars before any project import.

    Called at module scope (not as an autouse fixture) so the import
    chain triggered by ``tests/conftest.py`` later has every required
    env var already in ``os.environ`` — modules that construct
    ``ServerConfig()`` (or a subclass) during import need the env
    populated first or pydantic raises ``ValidationError``.
    """
    os.environ["PHYTOMNI_TESTING"] = "1"
    for name, value in _TEST_ENV.items():
        os.environ[name] = value


_install_test_environment()
