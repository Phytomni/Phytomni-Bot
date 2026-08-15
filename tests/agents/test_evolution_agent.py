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

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

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


async def test_find_spa_taxids_rejects_non_2xx_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
):
    """A non-2xx status cannot be treated as valid taxonomy evidence.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth loader.
        outbound_runtime: Recording process-owned outbound runtime.
    """

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(
        status=502,
        content=b'{"total":1,"records":[{"answer":"9606. Homo sapiens"}]}',
    )

    with pytest.raises(
        McpError, match="Evolution taxonomy lookup temporarily unavailable"
    ) as exc_info:
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )

    assert "species-secret" not in str(exc_info.value)
    assert "9606" not in str(exc_info.value)


async def test_find_spa_taxids_returns_empty_for_valid_direct_zero_result(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """A valid direct zero-result payload is the sole empty-result path."""

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(content=b'{"total":0,"records":[]}')

    assert (
        await evolution_agent.find_spa_taxids(
            "Arabidopsis", request_timeout=1.0
        )
        == []
    )


async def test_find_spa_taxids_returns_empty_for_valid_relay_zero_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid relay zero-result payload preserves the no-match outcome."""
    monkeypatch.setattr(evolution_agent, "relay_mode_enabled", lambda: True)

    async def get_json(_path: str, **_kwargs: Any) -> dict[str, Any]:
        """Return a structurally valid zero-result taxonomy response."""
        return {"total": 0, "records": []}

    monkeypatch.setattr(
        evolution_agent,
        "current_relay_client",
        lambda: SimpleNamespace(get_json=get_json),
    )

    assert (
        await evolution_agent.find_spa_taxids(
            "Arabidopsis", request_timeout=1.0
        )
        == []
    )


async def test_find_spa_taxids_rejects_direct_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """Malformed direct JSON uses the fixed redacted failure contract."""

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(
        content=b"not-json species-secret token-secret"
    )

    with pytest.raises(
        McpError, match="Evolution taxonomy lookup temporarily unavailable"
    ) as exc_info:
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )

    assert str(exc_info.value) == (
        "Evolution taxonomy lookup temporarily unavailable"
    )
    assert "species-secret" not in str(exc_info.value)
    assert "token-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"total": True, "records": []},
        {"total": -1, "records": []},
        {"total": 1, "records": {}},
        {"total": 1, "records": []},
        {"total": 0, "records": [{"answer": "9606. Homo sapiens"}]},
        {"total": 1, "records": [{}]},
        {"total": 1, "records": [{"answer": 9606}]},
        {"total": 1, "records": [{"answer": "not-a-taxid"}]},
    ],
)
async def test_find_spa_taxids_rejects_malformed_direct_payload(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    payload: dict[str, Any],
) -> None:
    """Malformed direct responses never become an empty taxonomy result."""

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(
        content=json.dumps(payload).encode("utf-8")
    )

    with pytest.raises(
        McpError, match="Evolution taxonomy lookup temporarily unavailable"
    ) as exc_info:
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )

    assert "species-secret" not in str(exc_info.value)


async def test_find_spa_taxids_rejects_direct_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """Direct transport failures use the fixed sanitized error contract."""

    async def fake_get_token(**_kwargs: Any) -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue_error(
        httpx.ConnectError("https://species-secret.example/?token=secret")
    )

    with pytest.raises(
        McpError, match="Evolution taxonomy lookup temporarily unavailable"
    ) as exc_info:
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )

    assert "species-secret" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


async def test_find_spa_taxids_rejects_relay_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay failures use the same fixed sanitized error contract."""
    monkeypatch.setattr(evolution_agent, "relay_mode_enabled", lambda: True)

    async def fail_lookup(_path: str, **_kwargs: Any) -> Any:
        """Raise a provider-shaped error containing sensitive test text."""
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="species-secret provider token-secret",
            )
        )

    relay = SimpleNamespace(get_json=fail_lookup)
    monkeypatch.setattr(evolution_agent, "current_relay_client", lambda: relay)

    with pytest.raises(
        McpError, match="Evolution taxonomy lookup temporarily unavailable"
    ) as exc_info:
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )

    assert "species-secret" not in str(exc_info.value)
    assert "token-secret" not in str(exc_info.value)


@pytest.mark.parametrize("relay", [False, True])
async def test_find_spa_taxids_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    relay: bool,
) -> None:
    """Cancellation is never projected as a taxonomy lookup failure."""
    del outbound_runtime
    monkeypatch.setattr(evolution_agent, "relay_mode_enabled", lambda: relay)

    async def cancel_lookup(_path: str, **_kwargs: Any) -> Any:
        """Raise cancellation from the selected provider boundary."""
        raise asyncio.CancelledError

    if relay:
        relay_client = SimpleNamespace(get_json=cancel_lookup)
        monkeypatch.setattr(
            evolution_agent, "current_relay_client", lambda: relay_client
        )
    else:

        async def cancel_token(**_kwargs: Any) -> str:
            """Raise cancellation while obtaining the direct token."""
            raise asyncio.CancelledError

        monkeypatch.setattr(evolution_agent, "get_token", cancel_token)

    with pytest.raises(asyncio.CancelledError):
        await evolution_agent.find_spa_taxids(
            "species-secret", request_timeout=1.0
        )


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
