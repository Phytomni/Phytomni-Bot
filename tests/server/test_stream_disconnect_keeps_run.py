# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP subscriber abort must not fail a healthy chat-completion run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import httpx
import pytest

from mcp_server_phytomni.api import streaming
from mcp_server_phytomni.api.app import _stream_chat_completion
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp.result_formatting import (
    run_finished,
    run_started,
    text_message_content,
)
from mcp_server_phytomni.runtime.live_tasks import (
    is_live_running,
    request_cancel,
)
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


async def _wait_for_run_status(
    db_path: str,
    run_id: str,
    *,
    owner: str,
    statuses: frozenset[str],
) -> Any:
    """Poll one run until it reaches an allowed status."""
    record = None
    try:
        async with asyncio.timeout(2.0):
            while True:
                record = RunRegistry(db_path).get_run(run_id, owner=owner)
                if record is not None and record.status in statuses:
                    return record
                await asyncio.sleep(0)
    except TimeoutError:
        status = None if record is None else record.status
        pytest.fail(
            f"run {run_id} status={status!r} not in {sorted(statuses)}"
        )
    raise AssertionError("unreachable")


async def _open_gated_chat_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    request_id: str,
) -> tuple[AsyncGenerator[str, None], str, asyncio.Event]:
    """Open one SSE body that pauses after the first content frame."""
    gate = asyncio.Event()
    captured: dict[str, str] = {}

    async def gated_stream(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        captured["run_id"] = run_id
        yield run_started(run_id, dialogue_id)
        yield text_message_content("m-keep", "partial")
        await gate.wait()
        yield text_message_content("m-keep", " leftover")
        yield run_finished(run_id)

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.prepare_tool_stream",
        gated_stream,
    )
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="keep going")],
        stream=True,
        dialogue_id="dlg-keep-run",
    )
    with request_context("u1", request_id):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={"user_query": "keep going", "obs_file_list": []},
            payload=payload,
            user_query="keep going",
        )
        body = cast(AsyncGenerator[str, None], response.body_iterator)
        async for line in body:
            if "event: TextMessageContent\n" not in line:
                continue
            return body, captured["run_id"], gate
    raise AssertionError("gated stream emitted no content frame")


async def test_http_subscriber_abort_keeps_run_until_tokens_finish(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the HTTP generator leaves leftover tokens on a live run."""
    body, run_id, gate = await _open_gated_chat_stream(
        monkeypatch,
        request_id="req-keep-run",
    )
    await body.aclose()
    with pytest.raises(StopAsyncIteration):
        await anext(body)

    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "running"
    assert is_live_running(run_id) is True

    gate.set()
    record = await _wait_for_run_status(
        tasks_db_path,
        run_id,
        owner="u1",
        statuses=frozenset({"succeeded"}),
    )
    assert record.result is not None
    assert record.result["formatted"]["answer"] == "partial leftover"
    assert record.result["partial"] is False
    assert is_live_running(run_id) is False


async def test_owner_cancel_still_settles_cancelled_draft(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner Stop still terminates the live producer and records cancelled."""
    body, run_id, _gate = await _open_gated_chat_stream(
        monkeypatch,
        request_id="req-owner-cancel",
    )
    request_cancel(run_id)
    await body.aclose()

    record = await _wait_for_run_status(
        tasks_db_path,
        run_id,
        owner="u1",
        statuses=frozenset({"cancelled"}),
    )
    assert record.result is not None
    assert record.result["formatted"]["answer"] == "partial"
    assert record.result["partial"] is True
    assert is_live_running(run_id) is False


async def test_abort_then_follow_run_stream_replays_leftover_tokens(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later subscriber replays the buffer and tails leftover tokens."""
    body, run_id, gate = await _open_gated_chat_stream(
        monkeypatch,
        request_id="req-follow-run",
    )
    await body.aclose()
    resumed = streaming.iter_run_stream(run_id, after=0)
    assert resumed is not None
    gate.set()
    lines = [line async for line in resumed]
    rendered = "".join(lines)
    assert "partial" in rendered
    assert "leftover" in rendered
    assert "event: RunFinished\n" in rendered
    record = await _wait_for_run_status(
        tasks_db_path,
        run_id,
        owner="u1",
        statuses=frozenset({"succeeded"}),
    )
    assert record.result is not None
    assert record.result["formatted"]["answer"] == "partial leftover"


async def test_get_run_stream_http_tails_after_subscriber_abort(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner GET /v1/runs/{id}/stream replays the buffer then live tail."""
    del tasks_db_path
    body, run_id, gate = await _open_gated_chat_stream(
        monkeypatch,
        request_id="req-http-follow",
    )
    await body.aclose()
    fetch = asyncio.create_task(
        api_client.get(
            f"/v1/runs/{run_id}/stream",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
    )
    await asyncio.sleep(0)
    gate.set()
    response = await fetch
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "leftover" in response.text
    assert "event: RunFinished\n" in response.text
