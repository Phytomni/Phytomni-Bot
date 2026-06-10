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

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_server_phytomni.agents.evolution import agent as evolution_agent

pytestmark = pytest.mark.agent


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


async def test_find_spa_taxids_uses_async_httpx_factory(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the taxid lookup drives the shared async httpx factory.

    The previous implementation called ``requests.get`` synchronously,
    blocking the event loop and bypassing the central TLS resolver.
    This test mocks ``get_async_client`` so the captured kwargs and the
    JSON parsing both surface through the new async path.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth
            token loader and the shared HTTP client factory.
    """
    captured: dict[str, Any] = {}

    async def fake_get_token() -> str:
        """Return a deterministic IAM token for the request headers."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)

    @asynccontextmanager
    async def fake_factory(**factory_kwargs: Any):
        """Hand out a stub client that records the parameters passed in."""
        captured["factory_kwargs"] = factory_kwargs

        class _Client:
            """Stub async httpx client whose ``get`` returns a fixed body."""

            async def get(self, url: str, **call_kwargs: Any) -> Any:
                """Capture the request and return a two-record payload."""
                captured["url"] = url
                captured["call_kwargs"] = call_kwargs
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "records": [
                            {"answer": "9606. Homo sapiens"},
                            {"answer": "10090. Mus musculus"},
                        ],
                    },
                )

        yield _Client()

    monkeypatch.setattr(evolution_agent, "get_async_client", fake_factory)

    taxids = await evolution_agent.find_spa_taxids("Arabidopsis", timeout=12.0)

    assert taxids == ["9606", "10090"]
    assert captured["factory_kwargs"]["timeout"] == 12.0
    assert captured["factory_kwargs"]["trust_env"] is False
    assert (
        captured["call_kwargs"]["headers"]["X-Auth-Token"] == "fake-iam-token"
    )
    assert captured["call_kwargs"]["params"]["question"] == "Arabidopsis"


async def test_find_spa_taxids_returns_empty_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the lookup short-circuits to an empty list on non-200.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth
            token loader and the shared HTTP client factory.
    """

    async def fake_get_token() -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)

    @asynccontextmanager
    async def fake_factory(**factory_kwargs: Any):
        """Yield a stub client that always returns a 502."""
        del factory_kwargs

        class _Client:
            """Stub async httpx client whose ``get`` always errors."""

            async def get(self, url: str, **call_kwargs: Any) -> Any:
                """Discard the request and return a 502 response."""
                del url, call_kwargs
                return httpx.Response(502, text="bad gateway")

        yield _Client()

    monkeypatch.setattr(evolution_agent, "get_async_client", fake_factory)

    taxids = await evolution_agent.find_spa_taxids("oryza", timeout=1.0)

    assert taxids == []
