# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP stream short-circuit tests for Chat A2UI confirm surfaces."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

# Pytest resolves this namespace-package helper; standalone pylint does not.
# pylint: disable=import-error
from tests.server.test_api_chat_streaming import (
    _extract_run_started_id,
    _patch_chat_stream,
)

# pylint: enable=import-error
from mcp_server_phytomni.agents.shared.a2ui import (
    A2UI_CUSTOM_NAME,
    author_a2ui_surface_offline,
)
from mcp_server_phytomni.api.app import _stream_chat_completion
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
def _use_offline_a2ui_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep HTTP streaming tests offline at the Surface Author seam."""

    async def _offline(ctx: Any) -> dict[str, Any]:
        return author_a2ui_surface_offline(ctx)

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.a2ui_graph.author_a2ui_surface",
        _offline,
    )


def _extract_custom_a2ui(body: str) -> dict[str, Any] | None:
    """Return the ``phyto.a2ui`` value from an SSE body, if present."""
    marker = "event: Custom\ndata: "
    for chunk in body.split("\n\n"):
        if not chunk.startswith(marker):
            continue
        payload = json.loads(chunk[len(marker) :])
        if payload.get("name") == A2UI_CUSTOM_NAME:
            value = payload.get("value")
            return value if isinstance(value, dict) else None
    return None


async def _consume_stream_until_a2ui(
    response: Any,
) -> tuple[str, dict[str, Any], str]:
    """Read SSE until ``phyto.a2ui``, then close before ``RunFinished``."""
    body_iter = cast(Any, response.body_iterator)
    accumulated = ""
    run_id = ""
    a2ui: dict[str, Any] | None = None
    async for line in body_iter:
        accumulated += line
        if not run_id and "event: RunStarted\n" in accumulated:
            run_id = _extract_run_started_id(accumulated)
        a2ui = _extract_custom_a2ui(accumulated)
        if a2ui is not None:
            await body_iter.aclose()
            break
    else:
        await body_iter.aclose()
    assert run_id
    assert a2ui is not None
    return run_id, a2ui, accumulated


async def test_stream_a2ui_confirm_settles_input_required(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag+confirm query emits phyto.a2ui and pauses the run."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="请确认是否继续分析",
        dialogue_id="dlg-a2ui",
    )
    assert response.status_code == 200
    body = response.text
    assert "event: RunStarted\n" in body
    assert f'"name": "{A2UI_CUSTOM_NAME}"' in body
    assert "event: RunFinished\n" in body
    assert body.rstrip().endswith("data: [DONE]")

    run_id = _extract_run_started_id(body)
    a2ui = _extract_custom_a2ui(body)
    assert a2ui is not None
    assert a2ui["widget"] == "confirm"
    assert a2ui["surface_id"]

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["status"] == "input_required"
    assert record["dialogue_id"] == "dlg-a2ui"
    result = record["result"]
    assert result is not None
    assert result["status"] == "input_required"
    interrupt = result["interrupt"]
    assert interrupt["thread_id"] == run_id
    assert interrupt["draft"]["a2ui"]["surface_id"] == a2ui["surface_id"]
    assert "[streamed]" not in json.dumps(result)


async def test_stream_a2ui_form_settles_input_required(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag+form query emits phyto.a2ui with widget=form and pauses."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")

    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="请填写基因名",
        dialogue_id="dlg-a2ui-form",
    )

    assert response.status_code == 200
    body = response.text
    assert "event: RunStarted\n" in body
    assert f'"name": "{A2UI_CUSTOM_NAME}"' in body
    assert "event: RunFinished\n" in body

    a2ui = _extract_custom_a2ui(body)
    assert a2ui is not None
    assert a2ui["widget"] == "form"
    assert a2ui["surface_id"]


async def test_stream_with_flag_skips_a2ui_for_non_confirm_query(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but non-confirm query uses normal chat stream."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_chat_stream(
        monkeypatch,
        [
            {
                "choices": [
                    {"delta": {"content": "OK"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="What is photosynthesis?",
    )

    assert response.status_code == 200
    body = response.text
    assert f'"name": "{A2UI_CUSTOM_NAME}"' not in body
    assert "event: TextMessageContent\n" in body


async def test_stream_without_flag_skips_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm-like query with flag off uses normal chat stream."""
    monkeypatch.delenv("PHYTOMNI_A2UI_ENABLED", raising=False)
    _patch_chat_stream(
        monkeypatch,
        [
            {
                "choices": [
                    {"delta": {"content": "OK"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="请确认是否继续",
    )

    assert response.status_code == 200
    body = response.text
    assert f'"name": "{A2UI_CUSTOM_NAME}"' not in body
    assert "event: TextMessageContent\n" in body


async def test_stream_a2ui_disconnect_after_a2ui_before_run_finished(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect after phyto.a2ui but before RunFinished stays paused."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")

    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="Confirm next step?")],
        stream=True,
        dialogue_id="dlg-a2ui-early-disc",
    )
    with request_context("u1", "req-a2ui-early-disc"):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={
                "user_query": "Confirm next step?",
                "obs_file_list": [],
            },
            payload=payload,
            user_query="Confirm next step?",
        )
        run_id, a2ui, accumulated = await _consume_stream_until_a2ui(response)

    assert "event: RunFinished\n" not in accumulated

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["status"] == "input_required"
    result = record["result"]
    assert result is not None
    assert result["status"] == "input_required"
    assert (
        result["interrupt"]["draft"]["a2ui"]["surface_id"]
        == a2ui["surface_id"]
    )

    registry = RunRegistry(db_path=tasks_db_path)
    assert registry.get_run(run_id, owner="u1") is not None


async def test_stream_a2ui_disconnect_after_run_finished_keeps_input_required(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect after RunFinished must not downgrade the run to failed."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")

    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="Confirm next step?")],
        stream=True,
        dialogue_id="dlg-a2ui-disc",
    )
    with request_context("u1", "req-a2ui-disc"):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={
                "user_query": "Confirm next step?",
                "obs_file_list": [],
            },
            payload=payload,
            user_query="Confirm next step?",
        )
        body_iter = cast(Any, response.body_iterator)
        run_id = ""
        async for line in body_iter:
            if "event: RunStarted\n" in line:
                run_id = _extract_run_started_id(line)
            if "event: RunFinished\n" in line:
                await body_iter.aclose()
                break
        else:
            await body_iter.aclose()

    registry = RunRegistry(db_path=tasks_db_path)
    record = registry.get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "input_required"
    assert record.result is not None
    assert record.result["status"] == "input_required"
