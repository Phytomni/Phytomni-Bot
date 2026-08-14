# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the evolution analysis wrapper.

Covers the happy path that resolves target species to taxonomy ids and
submits an evolution task, and the failure path where the chat client
returns no response and the wrapper short-circuits.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_server_phytomni.agents.evolution import agent as evolution_agent
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from tests.support.outbound_fakes import (
    QueueTransport,
    RecordingResources,
    assert_started_pool_attempts,
    recording_outbound_runtime,
)

pytestmark = pytest.mark.agent


class _LabeledQueueTransport(QueueTransport):
    """Record which runtime-owned HTTP profile reached the outer boundary."""

    def __init__(self, label: str, operations: list[str]) -> None:
        super().__init__()
        self.label = label
        self.operations = operations
        self.on_request: Callable[[], None] | None = None

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        """Record the profile before replaying its scripted response."""
        self.operations.append(self.label)
        if self.on_request is not None:
            self.on_request()
        return await super().handle_async_request(request)


async def test_evo_test_analysis_returns_none_task_when_chat_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify evo_test_analysis short-circuits when the chat returns None.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the chat
            and prompt loaders.

    Returns:
        None after the early-return assertion passes.
    """

    fake_chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": None})
    )

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return a deterministic prompt string for any lookup.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path within the YAML file.
            params: Optional prompt rendering parameters.

        Returns:
            Deterministic prompt string keyed on ``prompt_path``.
        """
        assert prompt_file
        assert params is None or isinstance(params, dict)
        return f"prompt:{prompt_path}"

    monkeypatch.setattr(
        evolution_agent, "_cached_chat_app", lambda: fake_chat_app
    )
    monkeypatch.setattr(evolution_agent, "get_prompt", fake_get_prompt)

    result = await evolution_agent.evo_test_analysis(
        query="ambiguous request",
        species_code="osa",
        gene_id="AtPHYB",
    )

    assert result == {"evolution_agents_task": None}
    chat_input = fake_chat_app.ainvoke.await_args.args[0]
    assert "user_query" in chat_input


async def test_public_spa_lookup_orders_iam_before_direct_upstream_pool() -> (
    None
):
    """The public SPA path completes IAM before one direct-profile lookup.

    This is intentionally two outbound operations, each exactly once: IAM
    runs on the trusted profile and releases before SPA FAQ acquires its own
    pool on the direct-upstream profile. Every other logical pool stays idle.
    """
    operations: list[str] = []
    trusted = _LabeledQueueTransport("trusted", operations)
    direct = _LabeledQueueTransport("direct_upstream", operations)
    trusted.enqueue(headers={"X-Subject-Token": "fake-iam-token"})
    direct.enqueue(
        content=(
            b'{"total":2,"records":['
            b'{"answer":"9606. Homo sapiens"},'
            b'{"answer":"10090. Mus musculus"}]}'
        )
    )
    resources = RecordingResources(
        transport={
            "trusted": trusted,
            "direct_upstream": direct,
        }
    )
    lease_order: list[tuple[int, int]] = []

    async with recording_outbound_runtime(
        config=ServerConfig(), resources=resources
    ) as runtime:
        direct.on_request = lambda: lease_order.append(
            (
                runtime.pools.snapshot(OutboundPoolName.IAM).in_use,
                runtime.pools.snapshot(OutboundPoolName.SPA_FAQ).in_use,
            )
        )

        taxids = await evolution_agent.find_spa_taxids(
            "Arabidopsis", request_timeout=12.0
        )

        assert taxids == ["9606", "10090"]
        assert operations == ["trusted", "direct_upstream"]
        assert lease_order == [(0, 1)]
        assert len(trusted.requests) == len(direct.requests) == 1
        request = direct.requests[0]
        assert request.headers["X-Auth-Token"] == "fake-iam-token"
        assert request.url.params["question"] == "Arabidopsis"
        assert resources.constructed["direct_upstream"]["trust_env"] is False
        assert_started_pool_attempts(
            runtime,
            {
                OutboundPoolName.IAM: 1,
                OutboundPoolName.SPA_FAQ: 1,
            },
        )
        assert runtime.pools.snapshot(OutboundPoolName.IAM).completed == 1
        assert runtime.pools.snapshot(OutboundPoolName.SPA_FAQ).completed == 1


async def test_find_spa_taxids_returns_empty_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
):
    """Verify the lookup short-circuits to an empty list on non-200.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth loader.
        outbound_runtime: Recording process-owned outbound runtime.
    """

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(status=502, content=b"bad gateway")

    taxids = await evolution_agent.find_spa_taxids(
        "oryza", request_timeout=1.0
    )

    assert taxids == []


@pytest.mark.parametrize("legacy_keyword", ["timeout"])
async def test_find_spa_taxids_rejects_legacy_timeout_keyword(
    outbound_runtime: Any,
    legacy_keyword: str,
) -> None:
    """The SPA lookup boundary rejects the removed timeout spelling.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
        legacy_keyword: Removed keyword spelling exercised dynamically.
    """
    del outbound_runtime
    legacy_options: dict[str, Any] = {legacy_keyword: 1.0}

    with pytest.raises(
        TypeError, match="unexpected keyword argument 'timeout'"
    ):
        await evolution_agent.find_spa_taxids("Arabidopsis", **legacy_options)
