# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for safe external capability normalization and discovery results."""

# pylint: disable=protected-access

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from mcp_server_phytomni.interop import capabilities as capability_module
from mcp_server_phytomni.interop.cache import DiscoveryCache
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryError,
    DiscoveryResult,
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


def _capability(**overrides: object) -> InteropCapability:
    """Build one valid capability with optional field overrides."""
    payload: dict[str, object] = {
        "target_id": "peer-cap",
        "kind": "mcp",
        "remote_name": "inspect",
        "qualified_name": "peer-cap__inspect",
        "description": "safe",
        "input_schema": {"type": "object"},
    }
    payload.update(overrides)
    return InteropCapability(**payload)  # type: ignore[arg-type]


def test_capability_rejects_invalid_kind_and_description() -> None:
    """Kind and description are validated before the DTO is frozen."""
    with pytest.raises(InteropCapabilityError, match="invalid_kind"):
        _capability(kind="other")
    with pytest.raises(InteropCapabilityError, match="invalid_description"):
        _capability(description=123)


def test_qualified_name_byte_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTF-8 qualified names stay inside the configured byte budget."""
    monkeypatch.setattr(capability_module, "MAX_QUALIFIED_NAME_BYTES", 5)
    with pytest.raises(
        InteropCapabilityError, match="qualified_name_too_large"
    ):
        _capability()


def test_json_schema_alias_returns_canonical_schema() -> None:
    """Consumers that ask for json_schema receive the frozen input schema."""
    capability = _capability()
    assert capability.json_schema == capability.input_schema
    assert capability.json_schema["type"] == "object"


def test_discovery_result_rejects_wrong_item_types() -> None:
    """Result containers refuse non-DTO members before sorting."""
    with pytest.raises(TypeError, match="capabilities"):
        DiscoveryResult(data=("nope",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DiscoveryError"):
        DiscoveryResult(errors=("nope",))  # type: ignore[arg-type]


def test_target_id_and_remote_name_validators() -> None:
    """Invalid identifiers are rejected at the DTO and helper seams."""
    with pytest.raises(InteropCapabilityError, match="invalid_target_id"):
        _capability(target_id="Nope")
    with pytest.raises(InteropCapabilityError, match="invalid_remote_name"):
        _capability(
            remote_name="bad name", qualified_name="peer-cap__bad name"
        )


def test_remote_name_recovery_and_missing() -> None:
    """Empty, prefixed-empty, and raw names stay deterministic."""
    with pytest.raises(InteropCapabilityError, match="missing_remote_name"):
        capability_module._remote_name(None, "peer-cap")
    with pytest.raises(InteropCapabilityError, match="missing_remote_name"):
        capability_module._remote_name("", "peer-cap")
    assert capability_module._remote_name("peer-cap__", "peer-cap") == (
        "peer-cap__"
    )
    assert capability_module._remote_name("inspect", "peer-cap") == "inspect"


def test_canonical_schema_rejects_bad_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-mappings, NaN, and non-object JSON stay invalid_schema."""
    with pytest.raises(InteropCapabilityError, match="invalid_schema"):
        capability_module._canonical_schema(
            "not-a-map"  # type: ignore[arg-type]
        )
    with pytest.raises(InteropCapabilityError, match="invalid_schema"):
        _capability(input_schema={"n": float("nan")})
    with pytest.raises(InteropCapabilityError, match="invalid_schema"):
        _capability(input_schema={"n": object()})

    monkeypatch.setattr(capability_module.json, "loads", lambda _: [1])
    with pytest.raises(InteropCapabilityError, match="invalid_schema"):
        capability_module._canonical_schema({"type": "object"})


def test_tool_schema_uses_schema_method_and_args() -> None:
    """Legacy schema() and args fallbacks feed the canonical encoder."""

    class _LegacySchema:
        """JSON-schema object without model_json_schema."""

        def schema(self) -> dict[str, object]:
            return {"type": "object", "properties": {"q": {"type": "string"}}}

    class _LegacyTool:
        """Tool exposing only a schema() args model."""

        name = "peer-cap_inspect"
        description = "legacy"
        args_schema = _LegacySchema()

    class _ArgsTool:
        """Tool exposing only an args mapping."""

        name = "peer-cap_inspect"
        description = "args"
        args_schema = None
        args = {"type": "object"}

    class _BadArgsTool:
        """Tool whose args value is not a mapping."""

        name = "peer-cap_inspect"
        description = "bad"
        args_schema = None
        args = "not-a-mapping"

    normalized = normalize_capabilities(
        "peer-cap", [cast(BaseTool, _LegacyTool())]
    )
    assert normalized[0].input_schema["properties"]["q"]["type"] == "string"
    normalized = normalize_capabilities(
        "peer-cap", [cast(BaseTool, _ArgsTool())]
    )
    assert normalized[0].input_schema["type"] == "object"
    with pytest.raises(InteropCapabilityError, match="invalid_schema"):
        normalize_capabilities("peer-cap", [cast(BaseTool, _BadArgsTool())])


def test_normalize_rejects_invalid_kind_and_description() -> None:
    """The public normalizer rejects kind and non-string descriptions."""

    class _BadDescription:
        """Tool whose description is not text."""

        name = "peer-cap_inspect"
        description = 123
        args_schema = None
        args: dict[str, object] = {}

    with pytest.raises(InteropCapabilityError, match="invalid_kind"):
        normalize_capabilities(
            "peer-cap",
            [_tool("peer-cap_inspect")],
            kind="x",
        )
    with pytest.raises(InteropCapabilityError, match="invalid_description"):
        normalize_capabilities("peer-cap", [cast(BaseTool, _BadDescription())])


async def test_unknown_target_kind_defaults_to_mcp() -> None:
    """A missing registry id still reports kind mcp on the error DTO."""
    result = await discover_external_mcp_capabilities(
        "missing-target",
        registry=InteropRegistry(_targets={}),
    )
    assert result.data == ()
    assert result.errors == (
        DiscoveryError("missing-target", "mcp", "unknown_target"),
    )


async def test_discovery_forwards_optional_kwargs_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional transport knobs and the cache seam are honored."""
    target = _target()
    seen: list[dict[str, Any]] = []

    async def load(*_: Any, **kwargs: Any) -> tuple[BaseTool, ...]:
        seen.append(kwargs)
        return (_tool("peer-cap_inspect"),)

    monkeypatch.setattr(capability_module, "load_external_mcp_tools", load)
    cache = DiscoveryCache(ttl_seconds=30)
    first = await discover_external_mcp_capabilities(
        target.id,
        registry=_registry(target),
        sensitive_config=object(),
        resolver=object(),
        _client_cls=object,
        cache=cache,
    )
    second = await discover_external_mcp_capabilities(
        target.id,
        registry=_registry(target),
        sensitive_config=object(),
        resolver=object(),
        _client_cls=object,
        cache=cache,
    )
    assert first is second
    assert len(seen) == 1
    assert "sensitive_config" in seen[0]
    assert "resolver" in seen[0]
    assert seen[0]["_client_cls"] is object


async def test_discovery_capability_and_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capability errors and unexpected exceptions stay target-level."""
    target = _target()

    async def bad_name(*_: Any, **__: Any) -> tuple[BaseTool, ...]:
        return (_tool("!!!invalid!!!"),)

    monkeypatch.setattr(capability_module, "load_external_mcp_tools", bad_name)
    result = await discover_external_mcp_capabilities(
        target.id, registry=_registry(target)
    )
    assert result.errors[0].code == "invalid_remote_name"

    async def boom(*_: Any, **__: Any) -> tuple[BaseTool, ...]:
        raise RuntimeError("peer stack")

    monkeypatch.setattr(capability_module, "load_external_mcp_tools", boom)
    result = await discover_external_mcp_capabilities(
        target.id, registry=_registry(target)
    )
    assert result.errors == (
        DiscoveryError(target.id, "mcp", "discovery_failed"),
    )
    assert "peer stack" not in repr(result)
