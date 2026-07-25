# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct contracts for the extracted HTTP streaming runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fastapi import HTTPException
from httpx import ConnectError, TimeoutException

from mcp_server_phytomni.api import streaming
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    run_finished,
    run_started,
    text_message_content,
)

pytestmark = pytest.mark.unit


def _disabled() -> bool:
    """Keep the direct ordinary-stream contract off the A2UI branch."""
    return False


def _no_widget(_query: str) -> str | None:
    """Return no A2UI widget for an ordinary stream."""
    return None


def _fixed_run_id(_prefix: str, _kind: str) -> str:
    """Return a deterministic id for the adapter contract."""
    return "run-direct-contract"


def _chat_slug(_model: str) -> str | None:
    """Map the direct contract model to its registry slug."""
    return "chat"


def _current_owner() -> str | None:
    """Return the owner captured by the fake request context."""
    return "owner-direct-contract"


def _current_request_id() -> str | None:
    """Return the request id captured by the fake request context."""
    return "request-direct-contract"


def _max_answer_bytes() -> int:
    """Keep the direct stream answer cap comfortably above the fixture."""
    return 1024


def _unused_runtime() -> Any:
    """Fail if the ordinary contract unexpectedly enters A2UI runtime."""
    raise AssertionError("ordinary stream unexpectedly requested A2UI runtime")


async def _direct_events(
    _tool_name: str,
    _arguments: dict[str, Any],
    *,
    run_id: str,
    dialogue_id: str | None,
) -> AsyncIterator[AguiEvent]:
    """Yield one complete typed stream for the adapter contract."""
    yield run_started(run_id, dialogue_id)
    yield text_message_content("direct-message", "adapter answer")
    yield run_finished(run_id)


def _dependencies(settlements: list[tuple[str, str, str, dict[str, Any]]]):
    """Build explicit request, A2UI, and persistence seams for one test."""

    def _record_run(
        run_id: str,
        agent: str,
        owner: str,
        request_info: Any,
    ) -> None:
        settlements.append((run_id, agent, owner, {"request": request_info}))

    def _settle(
        run_id: str,
        owner: str,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        settlements.append((run_id, owner, status, result))
        return True

    return streaming.StreamingDependencies(
        request=streaming.StreamingRequestDependencies(
            prepare_tool_stream=_direct_events,
            current_user=_current_owner,
            current_request_id=_current_request_id,
            new_run_id=_fixed_run_id,
            agent_slug=_chat_slug,
        ),
        a2ui=streaming.StreamingA2UIDependencies(
            enabled=_disabled,
            select_widget=_no_widget,
            runtime=_unused_runtime,
        ),
        persistence=streaming.StreamingPersistenceDependencies(
            create_running_stream_run=_record_run,
            settle_stream_run=_settle,
            stream_answer_max_bytes=_max_answer_bytes,
        ),
    )


def test_stream_setup_error_keeps_preopen_mapping() -> None:
    """Known setup failures retain their fixed HTTP boundary mapping."""
    assert (
        streaming.stream_setup_error(
            ConnectError("secret upstream"), priming=False
        ).status_code
        == 502
    )
    assert (
        streaming.stream_setup_error(
            TimeoutException("secret timeout"), priming=False
        ).status_code
        == 504
    )
    unsupported = streaming.stream_setup_error(
        NotImplementedError("internal"), priming=False
    )
    assert isinstance(unsupported, HTTPException)
    assert unsupported.status_code == 400


async def test_stream_runtime_uses_adapters_and_settles_answer() -> None:
    """The extracted runtime persists running then terminal state via seams."""
    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        dialogue_id="dialogue-direct-contract",
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={"user_query": "adapter query", "obs_file_list": []},
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body_iterator = cast(AsyncIterator[str], response.body_iterator)
    body = "".join([line async for line in body_iterator])

    assert "event: RunStarted\n" in body
    assert "adapter answer" in body
    assert body.rstrip().endswith("data: [DONE]")
    assert len(settlements) == 2
    assert settlements[0][0:3] == (
        "run-direct-contract",
        "chat",
        "owner-direct-contract",
    )
    assert settlements[1][0:3] == (
        "run-direct-contract",
        "owner-direct-contract",
        "succeeded",
    )
    assert settlements[1][3]["formatted"]["answer"] == "adapter answer"
