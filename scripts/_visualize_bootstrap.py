# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Install offline fake env and re-export mcp visualize symbols.

Mirrors the repo-root ``conftest.py`` bootstrap pattern: several
``mcp_server_phytomni`` modules construct ``ServerConfig()`` at
import time and would raise ``ValidationError`` if the deployment
endpoints / UUIDs are missing. This module installs the offline
fake env on import, then re-exports the three subgraph symbols the
``visualize_agent_graphs`` script needs. Consumers therefore
import everything from this module and never need a side-effect
import + suppression in their own top-of-file block.

The script invocation ``python scripts/visualize_agent_graphs.py``
puts ``scripts/`` on ``sys.path[0]`` automatically; the CLI test
loads the script via ``importlib.util.spec_from_file_location`` and
adds ``scripts/`` to ``sys.path`` itself for the same reason.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import Any

FAKE_ENV: dict[str, str] = {
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
    "TOKEN_URL": "https://example.invalid/token",
    "OBS_SERVER": "obs.example.invalid",
    "APP_ID": (
        '{"small": "viz-small-app",'
        ' "medium": "viz-medium-app",'
        ' "large": "viz-large-app"}'
    ),
}


def _install_fake_env() -> None:
    """Set every entry of :data:`FAKE_ENV` via ``setdefault``.

    Uses ``setdefault`` so an operator running the script with real
    deployment env vars exported keeps those values; only missing
    keys get the offline placeholder. The repo-root ``conftest.py``
    uses unconditional assignment instead because tests must always
    see the pytest fake env regardless of operator environment.
    """
    for name, value in FAKE_ENV.items():
        os.environ.setdefault(name, value)


_install_fake_env()

_SYMBOLS: dict[str, Any] = {}
SubgraphRegistry: Any
build_default_registry: Any
export_manifest: Any


def __getattr__(name: str) -> Any:
    """Load graph symbols only after the fake environment is installed."""
    if name not in {
        "SubgraphRegistry",
        "build_default_registry",
        "export_manifest",
    }:
        raise AttributeError(name)
    if name not in _SYMBOLS:
        graph_module = import_module("mcp_server_phytomni.graphs")
        if name == "build_default_registry":
            value = getattr(
                import_module("mcp_server_phytomni.graphs.defaults"), name
            )
        else:
            value = getattr(graph_module, name)
        _SYMBOLS[name] = value
    return _SYMBOLS[name]


__all__ = [
    "SubgraphRegistry",
    "build_default_registry",
    "export_manifest",
]
