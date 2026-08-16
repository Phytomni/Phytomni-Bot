# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for safe external capability normalization and discovery results."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from mcp_server_phytomni.interop import capabilities as capability_module
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryError,
    InteropCapability,
    InteropCapabilityError,
    discover_external_mcp_capabilities,
    normalize_capabilities,
)
from mcp_server_phytomni.interop.mcp_client import InteropMCPError
from mcp_server_phytomni.interop.models import MCPStreamableHttpTarget
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit


class _ToolInput(BaseModel):
    """Small JSON-schema-bearing fixture input."""

    query: str


async def _answer(query: str) -> str:
    """Return a deterministic fake remote answer."""
    return query


def _tool(name: str, *, description: str = "search remotely") -> BaseTool:
    """Build one temporary executable tool for the normalizer."""
    return StructuredTool.from_function(
        coroutine=_answer,
        name=name,
        description=description,
        args_schema=_ToolInput,
    )


def _target() -> MCPStreamableHttpTarget:
    """Build one validated target and matching enabled registry."""
    return MCPStreamableHttpTarget.model_validate(
        {
            "id": "peer-cap",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://cap.example.test/mcp",
            "allowed_tools": ["inspect"],
        }
    )


def _registry(target: MCPStreamableHttpTarget) -> InteropRegistry:
    """Build an enabled registry containing one target."""
    return InteropRegistry(_targets={target.id: target})


def test_normalize_capabilities_is_sorted_and_detached() -> None:
    """Qualified names and schemas are sorted, with no executable ref."""
    tools = [
        _tool("peer-cap_zeta"),
        _tool("peer-cap__inspect"),
    ]

    capabilities = normalize_capabilities("peer-cap", tools)

    assert [item.qualified_name for item in capabilities] == [
        "peer-cap__inspect",
        "peer-cap__zeta",
    ]
    assert capabilities[0].remote_name == "inspect"
    assert capabilities[0].kind == "mcp"
    assert capabilities[0].input_schema["type"] == "object"
    assert (
        json.loads(json.dumps(capabilities[0].model_dump()))["remote_name"]
        == "inspect"
    )
    assert not any(isinstance(value, BaseTool) for value in capabilities)


def test_capability_is_frozen_and_qualified_name_is_canonical() -> None:
    """The DTO cannot be reassigned or smuggle a different qualified name."""
    capability = InteropCapability(
        target_id="peer-cap",
        kind="mcp",
        remote_name="inspect",
        qualified_name="peer-cap__inspect",
        description="safe",
        input_schema={"type": "object"},
    )

    with pytest.raises(FrozenInstanceError):
        setattr(capability, "remote_name", "other")
    with pytest.raises(InteropCapabilityError, match="invalid_qualified_name"):
        InteropCapability(
            target_id="peer-cap",
            kind="mcp",
            remote_name="inspect",
            qualified_name="peer-cap_inspect",
            description="safe",
            input_schema={},
        )


def test_duplicate_qualified_names_are_rejected() -> None:
    """Official single/double separator aliases cannot collide silently."""
    with pytest.raises(
        InteropCapabilityError, match="qualified_name_conflict"
    ):
        normalize_capabilities(
            "peer-cap",
            [
                _tool("peer-cap_inspect"),
                _tool("peer-cap__inspect"),
            ],
        )


def test_metadata_limits_are_enforced() -> None:
    """Description, schema, and count limits reject unbounded peer metadata."""
    with pytest.raises(InteropCapabilityError, match="description_too_large"):
        normalize_capabilities(
            "peer-cap",
            [_tool("peer-cap_inspect", description="x" * 4097)],
        )

    with pytest.raises(InteropCapabilityError, match="schema_too_large"):
        InteropCapability(
            target_id="peer-cap",
            kind="mcp",
            remote_name="inspect",
            qualified_name="peer-cap__inspect",
            description="safe",
            input_schema={"payload": "x" * (64 * 1024)},
        )

    original_limit = capability_module.MAX_CAPABILITIES
    capability_module.MAX_CAPABILITIES = 1
    try:
        with pytest.raises(
            InteropCapabilityError, match="too_many_capabilities"
        ):
            normalize_capabilities(
                "peer-cap", [_tool("peer-cap_a"), _tool("peer-cap_b")]
            )
    finally:
        capability_module.MAX_CAPABILITIES = original_limit


async def test_discovery_projects_only_sanitized_error_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter and peer exception text never enters a discovery response."""
    target = _target()

    async def fail(*_: Any, **__: Any) -> tuple[BaseTool, ...]:
        raise InteropMCPError(
            "secret peer response",
            code="discovery_failed",
            target_id=target.id,
        )

    monkeypatch.setattr(capability_module, "load_external_mcp_tools", fail)
    result = await discover_external_mcp_capabilities(
        target.id, registry=_registry(target)
    )

    assert result.data == ()
    assert result.errors == (
        DiscoveryError(
            target_id="peer-cap", kind="mcp", code="discovery_failed"
        ),
    )
    assert "secret peer response" not in repr(result)


async def test_discovery_normalizes_one_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful adapter output is converted into the shared result DTO."""
    target = _target()

    async def load(*_: Any, **__: Any) -> tuple[BaseTool, ...]:
        return (_tool("peer-cap_inspect"),)

    monkeypatch.setattr(capability_module, "load_external_mcp_tools", load)
    result = await discover_external_mcp_capabilities(
        target.id, registry=_registry(target)
    )

    assert result.errors == ()
    assert result.data[0].qualified_name == "peer-cap__inspect"
