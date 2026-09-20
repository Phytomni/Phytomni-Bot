# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Execution entrypoint content, streaming, and operation tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.responses import StreamingResponse
from tests.support.execution_runtime_v2 import first_event_of_type

from mcp_server_phytomni.mcp.formatting.agui import (
    custom,
    text_message_content,
)
from mcp_server_phytomni.runtime.execution_content_stream_v2 import (
    execution_content_stream_for_db,
)
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
    invoke_public_agent_operation,
    invoke_public_agent_stream_response,
)
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    current_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import ExecutionEventType
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationNotFoundError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_data_result_publishes_the_complete_table_as_message_content(
    tmp_path: Path,
) -> None:
    """DataAgent keeps the legacy visible table instead of a count summary."""

    async def business_call() -> dict[str, object]:
        return {
            "result": {
                "formatted": {
                    "answer": "1 row x 1 column",
                    "tabular": {
                        "headers": ["transcript_id_1"],
                        "rows": [["Os01t0177400-01"]],
                    },
                }
            }
        }

    db_path = tmp_path / "data-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-data-result",
            agent_slug="data",
            arguments={"query": "list the transcript id"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-data-result", owner="alice", limit=30
    )
    assert page is not None
    completed = first_event_of_type(
        page.items,
        ExecutionEventType.MESSAGE_COMPLETED,
    )
    assert completed.to_public_dict()["public_payload"]["text"] == (
        '{"headers":["transcript_id_1"],"rows":[["Os01t0177400-01"]]}'
    )

    with sqlite_transaction(db_path) as connection:
        result_json = connection.execute(
            "SELECT result_json FROM runs WHERE execution_id = ?",
            ("turn-data-result",),
        ).fetchone()[0]
    assert '"answer":"1 row x 1 column"' in result_json
    assert '"tabular":{"headers":["transcript_id_1"]' in result_json


def test_data_result_chunks_unicode_table_without_losing_cells(
    tmp_path: Path,
) -> None:
    """A multibyte table remains complete under the 16 KiB event limit."""

    cell = "稻" * 7000

    async def business_call() -> dict[str, object]:
        return {
            "formatted": {
                "answer": "1 row x 1 column",
                "tabular": {"headers": ["description"], "rows": [[cell]]},
            }
        }

    db_path = tmp_path / "unicode-data-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-unicode-data-result",
            agent_slug="data",
            arguments={"query": "describe rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-unicode-data-result", owner="alice", limit=30
    )
    assert page is not None
    chunks = [
        event.to_public_dict()["public_payload"]["text"]
        for event in page.items
        if event.type.value in {"message.snapshot", "message.completed"}
    ]
    assert len(chunks) > 1
    assert json.loads("".join(chunks)) == {
        "headers": ["description"],
        "rows": [[cell]],
    }


def test_private_path_answer_is_returned_but_omitted_from_public_journal(
    tmp_path: Path,
) -> None:
    """Projection cannot make transport redaction a write prerequisite."""

    private_answer = "loaded /obs/private/chat.pdf"

    async def business_call() -> dict[str, object]:
        return {"formatted": {"answer": private_answer}}

    db_path = tmp_path / "private-answer.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-private-answer",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    assert value == {"formatted": {"answer": private_answer}}
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-private-answer", owner="alice", limit=30
    )
    assert page is not None
    rendered = " ".join(event.model_dump_json() for event in page.items)
    assert private_answer not in rendered
    assert "/obs/private/chat.pdf" not in rendered
    assert [event.type.value for event in page.items][-1] == (
        "execution.succeeded"
    )


def test_business_exception_is_rethrown_but_not_persisted(
    tmp_path: Path,
) -> None:
    """Verify business exception is rethrown but not persisted."""

    async def failing():
        raise ValueError("password=placeholder-business-error")

    db_path = tmp_path / "error.db"
    with pytest.raises(ValueError, match="placeholder-business-error"):
        asyncio.run(
            invoke_public_agent(
                db_path=str(db_path),
                owner="alice",
                execution_id="turn-error",
                agent_slug="chat",
                arguments={"query": "rice"},
                transport="http",
                call=failing,
            )
        )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-error", owner="alice", limit=20
    )
    assert page is not None
    encoded = str([event.to_public_dict() for event in page.items])
    assert "placeholder-business-error" not in encoded
    assert "business_execution_failed" in encoded


def test_nested_public_agent_inherits_execution_unless_new_turn_admitted(
    tmp_path: Path,
) -> None:
    """Verify nested public agent inherits execution unless new turn
    admitted."""

    db_path = tmp_path / "nested-agent.db"
    observed: list[tuple[str, str, str | None]] = []

    async def outer_business() -> dict[str, object]:
        async def inherited_business() -> str:
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            observed.append(
                (
                    boundary.context.execution_id,
                    boundary.context.agent.slug,
                    boundary.context.parent_span_id,
                )
            )
            return "inherited"

        inherited = await invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-ignored-for-nested-agent",
            agent_slug="knowledge",
            arguments={"query": "nested"},
            transport="nested_agent",
            call=inherited_business,
        )

        async def new_turn_business() -> str:
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            observed.append(
                (
                    boundary.context.execution_id,
                    boundary.context.agent.slug,
                    boundary.context.parent_span_id,
                )
            )
            return "new-turn"

        new_turn = await invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-explicit-child",
            agent_slug="data",
            arguments={"query": "separate"},
            transport="authenticated_http",
            call=new_turn_business,
            admit_new_user_turn=True,
        )
        return {"inherited": inherited, "new_turn": new_turn}

    result = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-parent",
            agent_slug="chat",
            arguments={"query": "parent"},
            transport="authenticated_http",
            call=outer_business,
        )
    )
    assert result == {"inherited": "inherited", "new_turn": "new-turn"}
    assert observed[0][0:2] == ("turn-parent", "knowledge")
    assert observed[0][2] is not None
    assert observed[1][0:2] == ("turn-explicit-child", "data")
    assert observed[1][2] is None

    repository = SQLiteExecutionReservationRepository(str(db_path))
    with pytest.raises(ExecutionReservationNotFoundError):
        repository.get(
            owner="alice", execution_id="turn-ignored-for-nested-agent"
        )
    assert (
        repository.get(
            owner="alice", execution_id="turn-explicit-child"
        ).status.value
        == "succeeded"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-parent", owner="alice", limit=50
    )
    assert page is not None
    agent_events = [
        event for event in page.items if event.source.value == "agent"
    ]
    assert [event.type.value for event in agent_events] == [
        "span.created",
        "span.started",
        "span.succeeded",
    ]
    assert all(
        event.public_payload.model_dump().get("phase") == "agent.knowledge"
        for event in agent_events
    )


def test_stream_response_settles_runtime_only_after_full_consumption(
    tmp_path: Path,
) -> None:
    """Verify stream response settles runtime only after full consumption."""

    observed_run_ids: list[str] = []

    async def build_response(run_id: str) -> StreamingResponse:
        observed_run_ids.append(run_id)

        async def body():
            custom(
                "phyto.progress",
                {"phase": "streaming", "current": 1, "total": 2},
            )
            text_message_content("message-1", "first")
            yield b"first"
            text_message_content("message-1", "second")
            yield b"second"

        response = StreamingResponse(body(), media_type="text/event-stream")
        setattr(
            response,
            "runtime_terminal_result",
            lambda: {"formatted": {"answer": "firstsecond"}},
        )
        return response

    async def exercise() -> list[bytes]:
        response = await invoke_public_agent_stream_response(
            db_path=str(tmp_path / "stream-success.db"),
            owner="alice",
            execution_id="turn-stream-success",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="openai_stream",
            call=build_response,
        )
        assert response.media_type == "text/event-stream"
        return [chunk async for chunk in response.body_iterator]

    assert asyncio.run(exercise()) == [b"first", b"second"]
    repository = SQLiteExecutionReservationRepository(
        str(tmp_path / "stream-success.db")
    )
    record = repository.get(owner="alice", execution_id="turn-stream-success")
    assert observed_run_ids == [record.run_id]
    assert record.status.value == "succeeded"
    persisted = RunRegistry(str(tmp_path / "stream-success.db")).get_run(
        record.run_id, owner="alice"
    )
    assert persisted is not None
    assert persisted.result is not None
    assert persisted.result["answer"] == "firstsecond"
    page = SQLiteExecutionJournal(
        str(tmp_path / "stream-success.db")
    ).list_events("turn-stream-success", owner="alice", limit=30)
    assert page is not None
    assert [event.type.value for event in page.items].count(
        "span.progress"
    ) == 1
    content = execution_content_stream_for_db(
        str(tmp_path / "stream-success.db")
    ).list_after(
        owner="alice",
        execution_id="turn-stream-success",
        output_revision=1,
        after_offset=0,
    )
    assert [(frame.offset, frame.delta) for frame in content] == [
        (5, "first"),
        (11, "second"),
    ]


def test_stream_disconnect_does_not_imply_execution_cancellation_or_failure(
    tmp_path: Path,
) -> None:
    """Verify stream disconnect does not imply execution cancellation or
    failure."""

    async def build_response(_run_id: str) -> StreamingResponse:
        async def body():
            yield b"first"
            yield b"unobserved"

        return StreamingResponse(body(), media_type="text/event-stream")

    async def exercise() -> None:
        response = await invoke_public_agent_stream_response(
            db_path=str(tmp_path / "stream-disconnect.db"),
            owner="alice",
            execution_id="turn-stream-disconnect",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="openai_stream",
            call=build_response,
        )
        iterator = aiter(response.body_iterator)
        assert await anext(iterator) == b"first"
        closer = getattr(iterator, "aclose")
        await closer()

    asyncio.run(exercise())
    record = SQLiteExecutionReservationRepository(
        str(tmp_path / "stream-disconnect.db")
    ).get(owner="alice", execution_id="turn-stream-disconnect")
    assert record.status.value == "running"


def test_cancelled_stream_response_build_stays_recoverable(
    tmp_path: Path,
) -> None:
    """Cancellation before a response exists must not synthesize FAILED."""

    db_path = str(tmp_path / "stream-build-cancelled.db")

    async def exercise() -> None:
        started = asyncio.Event()

        async def blocked_build(_run_id: str) -> StreamingResponse:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        task = asyncio.create_task(
            invoke_public_agent_stream_response(
                db_path=db_path,
                owner="alice",
                execution_id="turn-stream-build-cancelled",
                agent_slug="chat",
                arguments={"query": "rice"},
                transport="openai_stream",
                call=blocked_build,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    repository = SQLiteExecutionReservationRepository(db_path)
    current = repository.get(
        owner="alice", execution_id="turn-stream-build-cancelled"
    )
    assert current.status.value == "running"
    assert any(
        record.execution_id == "turn-stream-build-cancelled"
        for record in repository.list_recoverable(limit=10)
    )
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-stream-build-cancelled", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None


def test_cancelled_runtime_operation_stays_recoverable(
    tmp_path: Path,
) -> None:
    """Task cancellation must not be converted into a terminal FAILED fact."""

    db_path = str(tmp_path / "cancelled-operation.db")

    async def accepted():
        return {"status": "running"}, 202

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-cancelled-operation",
            agent_slug="research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=accepted,
        )
    )
    repository = SQLiteExecutionReservationRepository(db_path)
    baseline = repository.get(
        owner="alice", execution_id="turn-cancelled-operation"
    )

    async def exercise() -> None:
        started = asyncio.Event()

        async def blocked_operation():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            invoke_public_agent_operation(
                db_path=db_path,
                owner="alice",
                execution_id="turn-cancelled-operation",
                agent_slug="research",
                operation="recover",
                action_id="recover-cancelled",
                expected_revision=baseline.supervisor_revision,
                arguments={"source": "supervisor"},
                transport="supervisor",
                call=blocked_operation,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    current = repository.get(
        owner="alice", execution_id="turn-cancelled-operation"
    )
    assert current.status.value == "running"
    assert any(
        record.execution_id == "turn-cancelled-operation"
        for record in repository.list_recoverable(limit=10)
    )
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-cancelled-operation", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None


@pytest.mark.parametrize("operation", ["resume", "recover"])
def test_existing_operation_entry_preserves_business_response(
    tmp_path: Path,
    operation: str,
) -> None:
    """Verify existing operation entry preserves business response."""

    db_path = tmp_path / f"{operation}.db"

    async def admitted():
        return ({"status": "input_required"}, 202)

    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{operation}",
            agent_slug="review" if operation == "resume" else "research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=admitted,
        )
    )
    repository = SQLiteExecutionReservationRepository(str(db_path))
    record = repository.get(owner="alice", execution_id=f"turn-{operation}")

    async def business_operation():
        return (
            {
                "status": "succeeded",
                "unchanged": True,
                "result": {
                    "formatted": {
                        "answer": "durable recovery answer",
                    }
                },
            },
            200,
        )

    result = asyncio.run(
        invoke_public_agent_operation(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{operation}",
            agent_slug="review" if operation == "resume" else "research",
            operation=operation,
            action_id=f"action-{operation}",
            expected_revision=record.supervisor_revision,
            arguments={"approved": True},
            transport=operation,
            call=business_operation,
        )
    )
    assert result == (
        {
            "status": "succeeded",
            "unchanged": True,
            "result": {
                "formatted": {
                    "answer": "durable recovery answer",
                }
            },
        },
        200,
    )
    assert (
        repository.get(
            owner="alice", execution_id=f"turn-{operation}"
        ).status.value
        == "succeeded"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        f"turn-{operation}", owner="alice", limit=30
    )
    assert page is not None
    completed = [
        event
        for event in page.items
        if event.type.value == "message.completed"
    ]
    assert len(completed) == 1
    assert completed[0].public_payload.model_dump()["text"] == (
        "durable recovery answer"
    )
