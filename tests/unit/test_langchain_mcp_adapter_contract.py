# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the pinned LangChain MCP adapter dependency."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_adapter_dependency_is_pinned_to_the_supported_minor_range() -> None:
    """The official adapter stays on the API range this integration targets."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"langchain-mcp-adapters>=0.3,<0.4"' in pyproject


def test_client_uses_the_official_adapter_import_path() -> None:
    """The implementation must not vendor or reimplement the adapter."""
    source = (
        ROOT / "src/mcp_server_phytomni/interop/mcp_client.py"
    ).read_text(encoding="utf-8")

    assert "langchain_mcp_adapters.client" in source
    assert "MultiServerMCPClient" in source
    assert "tool_name_prefix=True" in source
    assert "handle_tool_errors=False" in source


def test_adapter_is_loaded_lazily_for_flag_off_startup() -> None:
    """Importing the interop package must not require the optional adapter."""
    source = (
        ROOT / "src/mcp_server_phytomni/interop/mcp_client.py"
    ).read_text(encoding="utf-8")

    assert "importlib.import_module" in source
    assert "from langchain_mcp_adapters" not in source
