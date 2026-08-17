# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Runtime contracts for conversation-context projection callables."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.conversation_context import (
    projection_protocols as proto,
)

pytestmark = pytest.mark.unit


def test_projection_protocol_module_exports_runtime_contracts() -> None:
    """Importing the protocol module runs TypedDict and Protocol bodies."""
    assert "selected_agent_id" in proto.ProjectionKwargs.__annotations__
    assert "observed_mode" in proto.RebuildKwargs.__annotations__
    assert proto.ProjectionBuilder.__name__ == "ProjectionBuilder"
    assert proto.RebuildBuilder.__name__ == "RebuildBuilder"
    assert proto.ProjectionBuilder.__qualname__.endswith("ProjectionBuilder")
    assert proto.RebuildBuilder.__qualname__.endswith("RebuildBuilder")
