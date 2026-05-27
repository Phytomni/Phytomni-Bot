# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``POST /v1/chat/completions`` with ``stream=true``.

Pins SSE framing, per-model gating, ``resolve_gene_id`` rejection, and
run-record finalization after the response stream drains. The route
emits ``text/event-stream`` data lines ending with ``[DONE]`` for
``phyto-chat`` and returns 400 for unsupported stream combinations.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, List

import httpx
import pytest

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _patch_chat_stream(
    monkeypatch: pytest.MonkeyPatch, payloads: List[Dict[str, Any]]
) -> None:
    """Replace ``stream_phyto_chat_chunks`` in the mcp app namespace.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        payloads: Provider chunk dicts the fake should yield.
    """

    async def fake_stream(**_kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        """Yield each pre-built payload, ignoring the chat kwargs."""
        for payload in payloads:
            yield payload

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)


def _parse_sse_body(body: str) -> List[str]:
    """Return the trimmed event lines (preserves ``[DONE]``)."""
    return [line for line in body.split("\n\n") if line]


async def test_stream_phyto_chat_returns_event_stream_with_done(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream=true`` + ``phyto-chat`` yields SSE event lines + ``[DONE]``.

    Pins the wire format: ``text/event-stream`` content-type, two
    upstream chunks become two ``data: {...}`` events plus the
    terminating ``data: [DONE]``. The ``model`` field is rewritten to
    the requested ``phyto-chat`` so the client sees the slug it asked
    for (mirrors ``to_chat_completion`` consistency).
    """
    payloads = [
        {
            "id": "ck1",
            "model": "deepseek-internal",
            "choices": [{"delta": {"content": "Hel"}}],
        },
        {
            "id": "ck1",
            "model": "deepseek-internal",
            "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
        },
    ]
    _patch_chat_stream(monkeypatch, payloads)

    response = await chat_completion(api_client, issued_api_key, stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_body(response.text)
    assert len(events) == 3
    assert events[-1] == "data: [DONE]"
    assert '"model": "phyto-chat"' in events[0]
    assert '"content": "Hel"' in events[0]


async def test_stream_with_resolve_gene_id_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """``resolve_gene_id=true`` + ``stream=true`` surfaces a 400.

    Pins the BriefGene-only gate: ``resolve_gene_id`` is valid only
    for BriefGene calls (``_maybe_resolve_brief_gene_query`` raises
    400 on misuse), and BriefGene is not in
    ``_STREAM_CAPABLE_TOOLS``. The combination is therefore always
    unreachable — ``resolve_gene_id=true`` against ``phyto-chat``
    yields the "resolve_gene_id is only valid for BriefGene calls"
    400, exercised here.
    """
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-chat",
        stream=True,
        resolve_gene_id=True,
    )

    assert response.status_code == 400
    assert "resolve_gene_id" in response.json()["error"]["message"]


async def test_stream_writes_run_record_on_completion(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """The run-record is written once after the stream drains.

    Pins the "stream end => run-record" contract: client history
    queries through ``GET /v1/runs`` must surface streamed calls
    alongside non-streamed ones. The recorded ``result`` carries the
    stream-mode marker (``"stream": True, "completed": True``) rather
    than the aggregated LLM content, because buffering the full
    response for the record would defeat the primitive's design.
    """
    _patch_chat_stream(
        monkeypatch,
        [
            {
                "id": "rec",
                "choices": [
                    {"delta": {"content": "Hi"}, "finish_reason": "stop"}
                ],
            },
        ],
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        dialogue_id="dlg-stream-1",
    )

    assert response.status_code == 200
    body = response.text
    assert "data: [DONE]" in body

    registry = RunRegistry(db_path=tasks_db_path)
    # issued_api_key fixture binds the key to user "u1"; the request
    # context resolves the owner from the authenticated principal.
    runs = registry.list_runs(owner="u1")
    chat_runs = [r for r in runs if r.spec.agent == "chat" and r.request_info]
    assert len(chat_runs) == 1
    record = chat_runs[0]
    assert record.request_info is not None
    assert record.request_info.dialogue_id == "dlg-stream-1"
    assert record.request_info.model == "phyto-chat"
    assert record.result is not None
    assert record.result.get("stream") is True
    assert record.result.get("completed") is True
