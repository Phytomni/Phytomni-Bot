# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Submission wrappers stay child outcomes and never become answer content."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    MessagePublicPayload,
)


def test_async_tool_completion_uses_a_bounded_submission_label() -> None:
    from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
        _tool_presentation,
    )

    presentation = _tool_presentation("GeneNetworkAgent")

    assert presentation.started_text == "Submitting analysis"
    assert presentation.succeeded_text == "Analysis submitted"
    assert presentation.failed_text == "Analysis submission failed"


def test_nested_async_ack_is_not_published_as_assistant_content(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = str(tmp_path / "nested-submission.db")
    provider_task_id = "provider-secret-task-123"
    acknowledgement = f"Task created successfully:{provider_task_id}"

    async def nested_submission():
        return {
            "result": {
                "formatted": {
                    "answer": acknowledgement,
                    "metadata": {
                        "task_id": provider_task_id,
                        "status": "RUNNING",
                    },
                },
                "execution": {
                    "tasks": [
                        {
                            "id": provider_task_id,
                            "accepted": True,
                            "status": "submitted",
                        }
                    ]
                },
            }
        }

    async def outer_call():
        return await invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="ignored-nested-identity",
            agent_slug="network",
            arguments={"goal": "private"},
            transport="nested",
            call=nested_submission,
        )

    returned = asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-nested-submission",
            agent_slug="knowledge",
            arguments={"query": "rice"},
            transport="test",
            call=outer_call,
        )
    )
    assert returned["result"]["formatted"]["answer"] == acknowledgement

    page = SQLiteExecutionJournal(db_path).list_events(
        "turn-nested-submission",
        owner="alice",
        limit=100,
    )
    assert page is not None
    public_events = [event.to_public_dict() for event in page.items]
    public_text = str(public_events)
    assert provider_task_id not in public_text
    assert acknowledgement not in public_text
    assert not any(
        event.type.value in {"message.snapshot", "message.completed"}
        for event in page.items
    )
    nested_terminal = next(
        event
        for event in page.items
        if event.type.value == "span.succeeded"
        and event.summary.key == "agent.network.succeeded"
    )
    assert nested_terminal.summary.text == "Analysis submitted"


def test_task_identifier_is_redacted_without_dropping_a_real_report(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = str(tmp_path / "report-redaction.db")
    provider_task_id = "provider-secret-task-456"

    async def report_call():
        return {
            "result": {
                "formatted": {
                    "answer": (
                        "# Network report\n\nThe analysis completed successfully. "
                        f"Internal task: {provider_task_id}."
                    ),
                    "metadata": {"task_id": provider_task_id},
                }
            }
        }

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-report-redaction",
            agent_slug="network",
            arguments={"goal": "rice network"},
            transport="test",
            call=report_call,
        )
    )
    page = SQLiteExecutionJournal(db_path).list_events(
        "turn-report-redaction",
        owner="alice",
        limit=100,
    )
    assert page is not None
    messages = [
        event
        for event in page.items
        if event.type.value in {"message.snapshot", "message.completed"}
    ]
    assert messages
    payloads = [event.public_payload for event in messages]
    assert all(
        isinstance(payload, MessagePublicPayload) for payload in payloads
    )
    message_text = "".join(
        payload.text
        for payload in payloads
        if isinstance(payload, MessagePublicPayload)
    )
    assert "# Network report" in message_text
    assert provider_task_id not in message_text
