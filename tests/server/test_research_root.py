# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the repository-owned HTTP Research root composition."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.api import research_root

pytestmark = pytest.mark.server


class _MetadataPort:
    """Structural metadata port accepted by the root factory."""

    async def resolve(self, _request: Any) -> tuple[Any, ...]:
        """Return no authorities for this composition-only test."""
        return ()

    async def verify(self, _request: Any) -> tuple[Any, ...]:
        """Return no authorities for this composition-only test."""
        return ()

    async def revoke(self, _request: Any) -> None:
        """Accept revocation without external storage."""
        return None


def test_default_root_factory_keeps_empty_remote_inspection_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted query keeps descriptions empty for remote inspection."""

    limits = SimpleNamespace(
        API_MAX_ATTACHMENTS_PER_REQUEST=4,
        API_MAX_RESEARCH_DATASET_PATHS=8,
        API_MAX_RESEARCH_INPUT_REFERENCES=12,
    )
    source = SimpleNamespace(
        BUCKET_NAME="research-bucket",
        OBS_SERVER="https://obs.example.invalid",
    )
    monkeypatch.setattr(research_root, "ApiLimitsConfig", lambda: limits)
    monkeypatch.setattr(research_root, "ServerConfig", lambda: source)

    admission = cast(
        Any,
        SimpleNamespace(
            owner="owner-1",
            parsed_input=parse_research_input("", "research-bucket"),
            managed_snapshot=(),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
        ),
    )
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )
    request = factory(admission)

    assert request.effective_query == ""
    assert request.inventory_request.parsed_input.effective_query == ""

    captured: dict[str, Any] = {}

    def join(inventory: Any, *, effective_query: str) -> str:
        captured["inventory"] = inventory
        captured["effective_query"] = effective_query
        return "prepared"

    monkeypatch.setattr(
        research_root,
        "prepare_research_input_for_remote_inspection",
        join,
    )
    join_prepared = request.dependencies.join_prepared
    assert join_prepared is not None
    assert join_prepared("inventory", "resolution") == "prepared"
    assert captured == {
        "inventory": "inventory",
        "effective_query": "",
    }

    resolve_descriptions = request.dependencies.resolve_descriptions
    assert resolve_descriptions is not None
    assert request.dependencies.plan_builder is not None

    async def check_resolution() -> None:
        assert await resolve_descriptions(request) is None

    asyncio.run(check_resolution())
