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

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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


async def test_find_spa_taxids_uses_direct_outbound_profile(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
):
    """Verify the taxid lookup uses the direct SPA pool profile.

    The previous implementation called ``requests.get`` synchronously,
    blocking the event loop and bypassing the central TLS resolver.
    This test installs a recording runtime so request construction and JSON
    parsing both surface through the owned direct-upstream profile.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth loader.
        outbound_runtime: Recording process-owned outbound runtime.
    """
    captured: dict[str, Any] = {}

    async def fake_get_token(**kwargs: Any) -> str:
        """Return a deterministic IAM token for the request headers."""
        captured["token_timeout"] = kwargs["request_timeout"]
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)
    outbound_runtime.transport.enqueue(
        content=(
            b'{"total":2,"records":['
            b'{"answer":"9606. Homo sapiens"},'
            b'{"answer":"10090. Mus musculus"}]}'
        )
    )

    taxids = await evolution_agent.find_spa_taxids(
        "Arabidopsis", request_timeout=12.0
    )

    assert taxids == ["9606", "10090"]
    assert captured["token_timeout"] == 12.0
    request = outbound_runtime.transport.requests[0]
    assert request.headers["X-Auth-Token"] == "fake-iam-token"
    assert request.url.params["question"] == "Arabidopsis"
    assert (
        outbound_runtime.resources.constructed["direct_upstream"]["trust_env"]
        is False
    )


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


async def test_find_spa_taxids_rejects_legacy_timeout_keyword(
    outbound_runtime: Any,
) -> None:
    """The SPA lookup boundary rejects the removed timeout spelling.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
    """
    del outbound_runtime
    legacy_options: dict[str, Any] = {"timeout": 1.0}

    with pytest.raises(
        TypeError, match="unexpected keyword argument 'timeout'"
    ):
        await evolution_agent.find_spa_taxids(
            "Arabidopsis", **legacy_options
        )
