# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OpenAI-compatible chat completions endpoint.

Covers auth enforcement, message flattening, ChatAgent passthrough with
follow_up_questions preserved, stream rejection, and unknown model.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _stub_chat(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    """Replace the ChatAgent handler with a canned ChatCompletion."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a fixed OpenAI-shaped completion."""
        captured["user_query"] = args.user_query
        return {
            "id": "chatcmpl-canned",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "photosynthesis converts light",
                        "follow_up_questions": ["what is C4?"],
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )


async def test_chat_completions_passthrough(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a valid call returns the ChatCompletion with extras."""
    captured: dict[str, Any] = {}
    _stub_chat(monkeypatch, captured)

    response = await chat_completion(
        api_client,
        issued_api_key,
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "what is photosynthesis?"},
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["id"]
    message = body["choices"][0]["message"]
    assert message["content"] == "photosynthesis converts light"
    assert body["formatted"]["follow_up_questions"] == ["what is C4?"]
    assert "raw" in body
    assert "what is photosynthesis?" in captured["user_query"]


async def test_chat_completions_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify a missing key yields the unified 401 envelope."""
    response = await api_client.post(
        "/v1/chat/completions",
        json={"model": "phyto-chat", "messages": []},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == 401


async def test_chat_completions_rejects_stream(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify stream=true is refused with 400."""
    response = await chat_completion(api_client, issued_api_key, stream=True)

    assert response.status_code == 400


async def test_chat_completions_unknown_model(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify an unknown model id yields 404."""
    response = await chat_completion(
        api_client, issued_api_key, model="gpt-imaginary"
    )

    assert response.status_code == 404


async def test_chat_completions_requires_user_message(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify an empty message list is rejected with 400."""
    response = await chat_completion(api_client, issued_api_key, messages=[])

    assert response.status_code == 400


async def test_chat_completions_records_local_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A successful chat completion writes one ``origin="local"`` run.

    Pin the 4b.2 HTTP-only behavior: the FastAPI path persists a fresh
    terminal-on-creation run row for sync agents so the upcoming
    ``/v1/runs`` endpoints can replay the answer, while the MCP stdio
    path (which never enters the API factory) keeps writing nothing.

    Args:
        api_client: In-process ASGI httpx client.
        issued_api_key: API key bound to user ``u1``.
        chat_completion: Factory issuing one authenticated POST.
        monkeypatch: Pytest monkeypatch fixture.
        tasks_db_path: Temp registry DB fixture wired into the API
            module's resolver.
    """
    _stub_chat(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="what is photosynthesis?",
    )
    assert response.status_code == 200

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.agent == "chat"
    assert record.spec.origin == "local"
    assert record.spec.user_id == "u1"
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.timestamps.expires_at is not None
    assert not record.task_ids


async def test_chat_completions_preserves_provider_reasoning_and_usage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasoner-style raw fields survive in choices.message and top level.

    When a provider returns ``reasoning_content`` on the message and
    ``usage`` / ``system_fingerprint`` / ``finish_reason`` at the top
    level, the envelope route keeps them at their OpenAI canonical
    positions so SDK clients reading them by name continue to work, and
    also exposes the full handler payload under ``raw`` for clients
    that want every provider-returned field.
    """

    async def fake(args: Any) -> dict[str, Any]:
        del args
        return {
            "id": "chatcmpl-reasoner",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Light energy is captured by chlorophyll.",
                        "reasoning_content": "Step 1: identify photons...",
                        "tool_calls": [],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 11,
                "total_tokens": 53,
            },
            "system_fingerprint": "fp_test",
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="why are leaves green?",
    )

    assert response.status_code == 200
    body = response.json()
    message = body["choices"][0]["message"]
    assert message["content"] == "Light energy is captured by chlorophyll."
    assert message["reasoning_content"] == "Step 1: identify photons..."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 42
    assert body["usage"]["total_tokens"] == 53
    assert body["system_fingerprint"] == "fp_test"
    assert body["raw"]["choices"][0]["message"]["reasoning_content"] == (
        "Step 1: identify photons..."
    )
    assert body["raw"]["usage"]["total_tokens"] == 53


async def test_chat_completions_envelope_carries_formatted_and_raw(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every chat completion body exposes top-level formatted and raw blocks.

    Locks the envelope contract end to end: the legacy duplicated top
    level (``follow_up_questions`` / ``references`` / ``metadata``)
    moved inside ``formatted``, ``raw`` carries the sanitized handler
    payload, and the ChatCompletion shape stays OpenAI compatible.
    """
    _stub_chat(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="hi",
    )

    assert response.status_code == 200
    body = response.json()
    assert "formatted" in body
    assert "raw" in body
    assert "follow_up_questions" not in body
    assert "references" not in body
    assert "metadata" not in body
    assert set(body["formatted"].keys()) == {
        "answer",
        "follow_up_questions",
        "metadata",
        "references",
        "tabular",
        "output_dirs",
    }
    assert isinstance(body["raw"], dict)
    assert body["raw"]["choices"][0]["message"]["content"] == (
        "photosynthesis converts light"
    )
