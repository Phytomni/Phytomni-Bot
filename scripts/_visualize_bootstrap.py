# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Install offline fake env for ``visualize_agent_graphs.py`` imports.

Mirrors the repo-root ``conftest.py`` bootstrap pattern: several
``mcp_server_phytomni`` modules construct ``ServerConfig()`` at
import time and would raise ``ValidationError`` if the deployment
endpoints / UUIDs are missing. Loading this module BEFORE the
``from mcp_server_phytomni...`` imports in
``visualize_agent_graphs.py`` lets those imports sit at the top of
the file (no ``E402`` / ``wrong-import-position`` exceptions).

The script invocation ``python scripts/visualize_agent_graphs.py``
puts ``scripts/`` on ``sys.path[0]`` automatically; the CLI test
loads the script via ``importlib.util.spec_from_file_location`` and
adds ``scripts/`` to ``sys.path`` itself for the same reason.
"""

from __future__ import annotations

import os

_FAKE_ENV: dict[str, str] = {
    "PHYTOMNI_TESTING": "1",
    "DOMAIN_NAME": "viz-domain",
    "USER_NAME": "viz-user",
    "USER_PASSWORD": "viz-password",
    "ACCESS_KEY_ID": "viz-access-key-id",
    "SECRET_ACCESS_KEY": "viz-secret-access-key",
    "BASE_URL": "https://example.invalid/llm",
    "MODEL_ID": "viz-model",
    "API_KEY": "viz-api-key",
    "CODER_URL": "https://example.invalid/coder",
    "CODER_MODEL": "viz-coder-model",
    "CODER_API_KEY": "viz-coder-api-key",
    "EMBED_URL": "https://example.invalid/embed",
    "EMBED_MODEL": "viz-embed-model",
    "EMBED_API_KEY": "viz-embed-api-key",
    "BI_TOKEN": "viz-bi-token",
    "RETRIEVE_URL": "https://example.invalid/retrieve",
    "RERANK_URL": "https://example.invalid/rerank",
    "CREATE_TASK_URL": "https://example.invalid/create-task",
    "UPDATE_TASK_URL": "https://example.invalid/update-task",
    "SPA_FAQ_URL": "https://example.invalid/repos/{repo_id}/faqs",
    "REPO_ID": "viz-repo-id",
    "REPO_ID_DICT": '{"viz-repo-id": 128}',
    "WORKSPACE_ID": "viz-workspace-id",
    "SUBJECT_ID": "viz-subject-id",
    "DATA_REPO_ID": "viz-data-repo-id",
    "TOOL_REPO_ID": "viz-tool-repo-id",
    "PROTOCOL_REPO_ID": "viz-protocol-repo-id",
    "SPA_REPO_ID": "viz-spa-repo-id",
    "DATABASE_URL": "https://example.invalid/database",
    "ANALYSIS_URL": "https://example.invalid/analysis",
    "BI_URL": "https://example.invalid/bi",
}


def _install_fake_env() -> None:
    """Set every entry of :data:`_FAKE_ENV` via ``setdefault``.

    Uses ``setdefault`` so an operator running the script with real
    deployment env vars exported keeps those values; only missing
    keys get the offline placeholder. The repo-root ``conftest.py``
    uses unconditional assignment instead because tests must always
    see the pytest fake env regardless of operator environment.
    """
    for name, value in _FAKE_ENV.items():
        os.environ.setdefault(name, value)


_install_fake_env()
