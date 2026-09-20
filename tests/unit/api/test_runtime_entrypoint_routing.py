# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Production transport seams delegate to ExecutionRuntime V2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    ResumeRequest,
)
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)


@pytest.mark.asyncio
async def test_openai_stream_response_is_built_inside_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify openai stream response is built inside runtime."""

    captured: dict[str, Any] = {}

    async def fake_stream(**request: Any) -> StreamingResponse:
        captured["runtime_run_id"] = request.get("runtime_run_id")

        async def body():
            yield b"data: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    async def fake_runtime(**request: Any) -> StreamingResponse:
        captured.update(
            {
                "owner": request["owner"],
                "execution_id": request["execution_id"],
                "agent_slug": request["agent_slug"],
                "transport": request["transport"],
            }
        )
        return await request["call"]("run-v2")

    monkeypatch.setattr(api_app, "_stream_chat_completion", fake_stream)
    monkeypatch.setattr(
        api_app, "invoke_public_agent_stream_response", fake_runtime
    )
    monkeypatch.setattr(api_app, "resolve_tasks_db_path", lambda: "ignored.db")
    monkeypatch.setattr(
        getattr(api_app, "_request_context"),
        "current_request_user",
        lambda: "alice",
    )

    response = await getattr(api_app, "_stream_chat_response")(
        tool_name="ChatAgent",
        arguments={"user_query": "rice", "obs_file_list": []},
        payload=ChatCompletionRequest(
            model="phytomni-chat",
            messages=[ChatMessage(role="user", content="rice")],
            stream=True,
        ),
        user_query="rice",
        execution_id="turn-openai-stream",
    )

    assert isinstance(response, StreamingResponse)
    assert captured == {
        "owner": "alice",
        "execution_id": "turn-openai-stream",
        "agent_slug": "chat",
        "transport": "openai_stream",
        "runtime_run_id": "run-v2",
    }


@pytest.mark.asyncio
async def test_review_resume_uses_runtime_operation_for_v2_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify review resume uses runtime operation for V2 run."""

    db_path = tmp_path / "resume-routing.db"

    async def waiting():
        return ({"status": "input_required"}, 202)

    await invoke_public_agent(
        db_path=str(db_path),
        owner="alice",
        execution_id="turn-review",
        agent_slug="review",
        arguments={"query": "rice"},
        transport="authenticated_http",
        call=waiting,
    )
    reservation = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id="turn-review"
    )

    async def domain_resume(**_request: Any) -> tuple[dict[str, Any], int]:
        return {"status": "succeeded", "answer": "unchanged"}, 200

    monkeypatch.setattr(api_app, "resolve_tasks_db_path", lambda: str(db_path))
    monkeypatch.setattr(
        getattr(api_app, "_request_context"),
        "current_request_user",
        lambda: "alice",
    )
    monkeypatch.setattr(
        api_app.a2ui_runtime, "resume_review_run", domain_resume
    )

    result = await getattr(api_app, "_resume_review_run")(
        thread_id=reservation.run_id,
        payload=ResumeRequest(approved=True),
    )

    assert result == ({"status": "succeeded", "answer": "unchanged"}, 200)
    assert (
        SQLiteExecutionReservationRepository(str(db_path))
        .get(owner="alice", execution_id="turn-review")
        .status.value
        == "succeeded"
    )
