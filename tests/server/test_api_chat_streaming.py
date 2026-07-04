# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``POST /v1/chat/completions`` with ``stream=true``.

Pins AG-UI SSE framing (``RunStarted`` / ``TextMessageContent`` /
``RunFinished`` plus the trailing ``[DONE]``), per-model gating,
``resolve_gene_id`` rejection, and the two-stage run write: a
``running`` row is written before the first frame and settled to
``succeeded``/``failed`` from the stream wrapper's ``finally`` block
once the response drains.
"""

from __future__ import annotations

import json
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


def _extract_run_started_id(body: str) -> str:
    """Return the ``run_id`` embedded in the SSE body's RunStarted frame."""
    marker = "event: RunStarted\ndata: "
    start = body.index(marker) + len(marker)
    end = body.index("\n", start)
    return str(json.loads(body[start:end])["run_id"])


async def test_stream_phyto_chat_emits_agui_frames(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream=true`` + ``phyto-chat`` yields AG-UI SSE frames.

    Pins the wire format: ``text/event-stream`` content-type, an
    opening ``RunStarted`` frame, at least one ``TextMessageContent``
    delta frame per non-empty upstream chunk, a closing
    ``RunFinished`` frame, and the terminating ``data: [DONE]`` line
    every SSE consumer relies on to close its ``EventSource``.
    """
    _patch_chat_stream(
        monkeypatch,
        [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {
                "choices": [
                    {"delta": {"content": "lo"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(api_client, issued_api_key, stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: RunStarted\n" in body
    assert "event: TextMessageContent\n" in body
    assert "event: RunFinished\n" in body
    assert body.rstrip().endswith("data: [DONE]")


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


async def test_stream_run_settles_succeeded_after_finish(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """The run settles ``succeeded`` after the stream reaches RunFinished.

    Pins the two-stage run write: stage 1 pre-mints ``run_id`` and
    writes a ``running`` row before the first frame; stage 2 settles
    the same row to ``succeeded`` from the ``finally`` block once the
    wrapper observes the ``RunFinished`` marker. Client history
    queries through ``GET /v1/runs`` must surface exactly one row for
    the call, carrying the request-scoped ``dialogue_id`` and the
    ``origin="local"`` stamp sync agents use.

    Also cross-checks two settle-path contracts: the ``run_id`` carried
    by the opening ``RunStarted`` SSE frame is the same id the registry
    persisted (not a placeholder unrelated to the polled row), and the
    settled row's ``created_at`` was never advanced past its own
    ``updated_at`` — the regression this test guards settled the run
    via ``create_run``'s INSERT OR REPLACE, which stamps ``now`` into
    both columns and would otherwise let a stale ``created_at`` slip
    past ``updated_at`` on a slow settle.
    """
    _patch_chat_stream(
        monkeypatch,
        [
            {
                "choices": [
                    {"delta": {"content": "Hi"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(
        api_client, issued_api_key, stream=True, dialogue_id="dlg-s1"
    )

    assert response.status_code == 200
    started_run_id = _extract_run_started_id(response.text)
    registry = RunRegistry(db_path=tasks_db_path)
    # issued_api_key fixture binds the key to user "u1"; the request
    # context resolves the owner from the authenticated principal.
    runs = [
        r
        for r in registry.list_runs(owner="u1")
        if r.spec.agent == "chat" and r.request_info
    ]
    assert len(runs) == 1
    record = runs[0]
    assert record.status == "succeeded"
    assert record.request_info.dialogue_id == "dlg-s1"
    assert record.spec.origin == "local"
    assert record.spec.run_id == started_run_id
    assert record.timestamps.created_at <= record.timestamps.updated_at
