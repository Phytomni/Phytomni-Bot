# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared setup for execution V2 HTTP contract tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from mcp_server_phytomni.public_agent_catalog import public_agent_spec
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    ExecutionJournal,
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionEventV2,
    parse_execution_event_intent_v2,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import (
    build_root_span_spec,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
)

_SERVICE_TOKEN = "execution-service-token"


def _append_event(
    journal: ExecutionJournal,
    record: ExecutionReservationRecord,
    *,
    owner: str,
    value: object,
) -> ExecutionEventV2:
    return journal.append(
        record.execution_id,
        owner=owner,
        intent=parse_execution_event_intent_v2(value),
    )


def execution_v2_admission(**overrides: object) -> dict[str, object]:
    """Build the canonical service-admission request body."""
    payload: dict[str, object] = {
        "schema_version": 2,
        "owner_ref": "u1",
        "execution_id": "turn-v2-admission",
        "fingerprint_version": 1,
        "fingerprint": "a" * 64,
        "agent_slug": "chat",
        "arguments": {"query": "rice"},
    }
    payload.update(overrides)
    return payload


def execution_v2_headers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner: str | None = None,
) -> dict[str, str]:
    """Enable service auth and return its canonical request headers."""
    monkeypatch.setenv("API_SERVICE_TOKEN", _SERVICE_TOKEN)
    headers = {"X-Service-Token": _SERVICE_TOKEN}
    if owner is not None:
        headers["X-Phyto-Owner"] = owner
    return headers


def execution_user_headers(
    api_key: str,
    *,
    last_event_id: str | None = None,
) -> dict[str, str]:
    """Return user-authenticated execution API request headers."""
    headers = {"Authorization": f"Bearer {api_key}"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    return headers


def seed_execution_v2(
    tasks_db_path: str,
    *,
    owner: str,
    execution_id: str,
    agent_slug: str = "chat",
) -> tuple[ExecutionReservationRecord, ExecutionEventV2, ExecutionEventV2]:
    """Seed one running execution and its initial public artifact event."""
    repository = SQLiteExecutionReservationRepository(tasks_db_path)
    record = repository.reserve(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint="c" * 64,
        command=ExecutionCommand(
            agent_slug=agent_slug,
            arguments={"query": "rice"},
        ),
    )
    spec = public_agent_spec(agent_slug)
    assert spec is not None
    SQLiteExecutionWorkRepository(tasks_db_path).create_span(
        build_root_span_spec(record, spec)
    )
    journal = SQLiteExecutionJournal(tasks_db_path)
    started = _append_event(
        journal,
        record,
        owner=owner,
        value={
            "type": "execution.started",
            "status": "running",
            "source": "runtime",
            "span_id": record.root_span_id,
            "summary": {
                "key": "execution.started",
                "text": "Execution started",
            },
            "public_payload": {},
        },
    )
    published = append_artifact_published(
        journal,
        record,
        owner=owner,
        summary=("artifact.published", "Result published"),
        artifact=("report.md", "text/markdown", 12, "artifact-report"),
    )
    return record, started, published


def append_artifact_published(
    journal: ExecutionJournal,
    record: ExecutionReservationRecord,
    *,
    owner: str,
    summary: tuple[str, str],
    artifact: tuple[str, str, int, str],
) -> ExecutionEventV2:
    """Append a successful public artifact event for a reservation."""
    summary_key, summary_text = summary
    name, media_type, size_bytes, target_id = artifact
    return _append_event(
        journal,
        record,
        owner=owner,
        value={
            "type": "artifact.published",
            "status": "succeeded",
            "source": "artifact",
            "span_id": record.root_span_id,
            "summary": {"key": summary_key, "text": summary_text},
            "public_payload": {
                "name": name,
                "media_type": media_type,
                "size_bytes": size_bytes,
            },
            "target": {"kind": "artifact", "id": target_id},
        },
    )


def append_execution_succeeded(
    journal: ExecutionJournal,
    record: ExecutionReservationRecord,
    *,
    owner: str,
    summary_key: str = "execution.succeeded",
    summary_text: str = "Execution succeeded",
) -> ExecutionEventV2:
    """Append the canonical successful terminal event for a reservation."""
    return _append_event(
        journal,
        record,
        owner=owner,
        value={
            "type": "execution.succeeded",
            "status": "succeeded",
            "source": "runtime",
            "span_id": record.root_span_id,
            "summary": {
                "key": summary_key,
                "text": summary_text,
            },
            "public_payload": {},
        },
    )


def one_shot_sleep_callback(
    action: Callable[[], object],
) -> Callable[[float], Awaitable[None]]:
    """Adapt a synchronous settlement action to one idempotent sleep hook."""
    invoked = False

    async def invoke_once(_seconds: float) -> None:
        nonlocal invoked
        if invoked:
            return
        invoked = True
        action()

    return invoke_once


def execution_succeeded_sleep_callback(
    journal: ExecutionJournal,
    record: ExecutionReservationRecord,
    *,
    owner: str,
) -> Callable[[float], Awaitable[None]]:
    """Return a one-shot sleep hook that commits successful settlement."""

    def settle() -> None:
        append_execution_succeeded(journal, record, owner=owner)

    return one_shot_sleep_callback(settle)


__all__ = [
    "append_artifact_published",
    "append_execution_succeeded",
    "execution_user_headers",
    "execution_succeeded_sleep_callback",
    "execution_v2_admission",
    "execution_v2_headers",
    "one_shot_sleep_callback",
    "seed_execution_v2",
]
