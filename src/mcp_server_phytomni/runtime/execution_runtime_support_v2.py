# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small value helpers shared by the canonical Execution Runtime."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import NotRequired, TypedDict

from .execution_journal_v2 import (
    EventStatus,
    ExecutionEventType,
    ExecutionStatus,
    TrackingHealth,
)
from .execution_log_artifact_v2 import ExecutionLogArtifactV2
from .execution_reservation_v2 import ExecutionReservationRecord
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionArtifactRef,
    ExecutionCommand,
    ExecutionContext,
)
from .execution_target_store_v2 import ExecutionTargetBindingV2

_PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class RuntimeEventPublication:
    """Complete bounded fields for one Runtime event publication."""

    event_type: ExecutionEventType
    status: EventStatus
    payload: dict[str, object]
    summary: tuple[str, str]
    operation_token: str
    source: str = "runtime"
    target: dict[str, str] | None = None


class RuntimeAppendFields(TypedDict):
    """Keyword fields accepted by the Runtime journal appender."""

    event_type: ExecutionEventType
    status: EventStatus
    payload: dict[str, object]
    summary_key: str
    summary_text: str
    operation_token: NotRequired[str]
    source: NotRequired[str]
    target: NotRequired[dict[str, str] | None]


@dataclass(frozen=True, slots=True)
class _MessagePublicationPlan:
    outcome: DriverOutcome
    message_id: str
    content: str
    chunks: tuple[str, ...]
    content_sha256: str
    references: tuple[Mapping[str, str | bool], ...]
    operation_token: str

    def events(self) -> tuple[RuntimeEventPublication, ...]:
        """Expand the bounded message into ordered snapshot events."""
        publications: list[RuntimeEventPublication] = []
        base_offset = 0
        for chunk_index, text in enumerate(self.chunks):
            publication = self._event(chunk_index, text, base_offset)
            publications.append(publication)
            base_offset += len(text)
        return tuple(publications)

    def _event(
        self,
        chunk_index: int,
        text: str,
        base_offset: int,
    ) -> RuntimeEventPublication:
        """Build one snapshot or terminal message event."""
        completed = (
            self.outcome.terminal and chunk_index == len(self.chunks) - 1
        )
        output_revision = max(1, self.outcome.provider_revision)
        event_type = (
            ExecutionEventType.MESSAGE_COMPLETED
            if completed
            else ExecutionEventType.MESSAGE_SNAPSHOT
        )
        offset = base_offset + len(text)
        payload: dict[str, object] = {
            "output_revision": output_revision,
            "message_id": self.message_id,
            "source_message_id": self.message_id,
            "base_offset": base_offset,
            "offset": offset,
            "total_length": len(self.content),
            "chunk_index": chunk_index,
            "chunk_count": len(self.chunks),
            "content_sha256": self.content_sha256,
            "text": text,
        }
        if completed and self.references:
            payload["references"] = list(self.references)
        return RuntimeEventPublication(
            event_type=event_type,
            status=EventStatus(self.outcome.status.value),
            payload=payload,
            summary=(
                event_type.value,
                (
                    "Message completed"
                    if completed
                    else "Message snapshot updated"
                ),
            ),
            operation_token=(
                f"{self.operation_token}:message:{output_revision}:"
                f"{chunk_index}"
            ),
            source="message",
        )


def build_execution_log_target_binding(
    *,
    owner: str,
    execution_id: str,
    artifact: ExecutionLogArtifactV2,
) -> ExecutionTargetBindingV2:
    """Bind one private execution log behind its opaque public target."""
    return ExecutionTargetBindingV2(
        owner=owner,
        execution_id=execution_id,
        kind="artifact",
        target_id=artifact.target_id,
        role="execution_log",
        name=artifact.name,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        delivery_ref=artifact.delivery_ref,
    )


def assistant_message_id(
    context: ExecutionContext,
    command: ExecutionCommand,
    *,
    admitted_message_id: str | None = None,
) -> str:
    """Resolve the Web-admitted assistant identity or a stable fallback."""
    for candidate in (
        command.arguments.get("__assistant_message_id"),
        admitted_message_id,
    ):
        if isinstance(candidate, str) and _PUBLIC_IDENTIFIER.fullmatch(
            candidate
        ):
            return candidate
    digest = hashlib.sha256(
        context.execution_id.encode("utf-8", errors="strict")
    ).hexdigest()[:32]
    return f"msg:assistant:{digest}"


def utf8_bounded_chunks(value: str, *, max_bytes: int) -> tuple[str, ...]:
    """Split at Unicode boundaries while keeping encoded chunks finite."""
    if max_bytes < 4:
        raise ValueError("message chunk byte limit is too small")
    chunks: list[str] = []
    start = 0
    chunk_bytes = 0
    for index, character in enumerate(value):
        character_bytes = len(character.encode("utf-8", errors="strict"))
        if chunk_bytes and chunk_bytes + character_bytes > max_bytes:
            chunks.append(value[start:index])
            start = index
            chunk_bytes = 0
        chunk_bytes += character_bytes
    if start < len(value):
        chunks.append(value[start:])
    return tuple(chunks)


def build_message_publications(
    context: ExecutionContext,
    outcome: DriverOutcome,
    command: ExecutionCommand,
    operation_token: str,
    admitted_message_id: str | None,
) -> tuple[RuntimeEventPublication, ...]:
    """Build ordered public message events for one Driver result."""
    result = outcome.result
    if result is None:
        return ()
    content = (
        json.dumps(
            result.tabular.to_public_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if result.tabular is not None
        else result.answer
    )
    if not content:
        return ()
    chunks = (
        utf8_bounded_chunks(content, max_bytes=4096)
        if result.tabular is not None
        else tuple(
            content[slice(index, index + 8192)]
            for index in range(0, len(content), 8192)
        )
    )
    return _MessagePublicationPlan(
        outcome=outcome,
        message_id=assistant_message_id(
            context,
            command,
            admitted_message_id=admitted_message_id,
        ),
        content=content,
        chunks=chunks,
        content_sha256=hashlib.sha256(
            content.encode("utf-8", errors="strict")
        ).hexdigest(),
        references=result.references,
        operation_token=operation_token,
    ).events()


def build_artifact_target_binding(
    context: ExecutionContext,
    artifact: ExecutionArtifactRef,
) -> ExecutionTargetBindingV2:
    """Build the private delivery binding for one public artifact target."""
    assert artifact.private_delivery_ref is not None
    return ExecutionTargetBindingV2(
        owner=context.owner_ref,
        execution_id=context.execution_id,
        kind=str(artifact.target_kind),
        target_id=artifact.target_id,
        role=artifact.role,
        name=artifact.name or artifact.role,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        delivery_ref=artifact.private_delivery_ref,
    )


def build_artifact_publication(
    outcome: DriverOutcome,
    artifact: ExecutionArtifactRef,
) -> RuntimeEventPublication:
    """Build the public event for one transport-neutral artifact."""
    target_kind = str(artifact.target_kind)
    event_type = (
        ExecutionEventType.ARTIFACT_PUBLISHED
        if target_kind in {"artifact", "download"}
        else ExecutionEventType.RESULT_PUBLISHED
    )
    return RuntimeEventPublication(
        event_type=event_type,
        status=EventStatus(outcome.status.value),
        payload=artifact_public_payload(artifact),
        summary=(f"result.{artifact.role}.published", "Result published"),
        operation_token=f"resource:{target_kind}:{artifact.target_id}",
        source="artifact",
        target={"kind": target_kind, "id": artifact.target_id},
    )


def artifact_public_payload(
    artifact: ExecutionArtifactRef | ExecutionLogArtifactV2,
) -> dict[str, object]:
    """Return the bounded public metadata common to artifact events."""
    return {
        "name": artifact.name,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
    }


def reservation_outcome(record: ExecutionReservationRecord) -> DriverOutcome:
    """Rebuild the bounded replay outcome for an existing reservation."""
    if record.status is ExecutionStatus.FAILED:
        return DriverOutcome.failed(code="execution_failed")
    return DriverOutcome(status=record.status)


def reservation_deadline_exceeded(
    record: ExecutionReservationRecord,
    now: datetime,
) -> bool:
    """Return whether a non-waiting reservation passed its deadline."""
    if record.status is ExecutionStatus.WAITING_INPUT:
        return False
    deadline = datetime.fromisoformat(
        record.deadline_at.replace("Z", "+00:00")
    )
    return now >= deadline


def public_action_surface_id(command: ExecutionCommand) -> str:
    """Prefer the validated surface, otherwise derive an opaque safe id."""
    candidate = command.arguments.get("surface_id")
    if isinstance(candidate, str) and _PUBLIC_IDENTIFIER.fullmatch(candidate):
        return candidate
    action_id = command.action_id or "action"
    if _PUBLIC_IDENTIFIER.fullmatch(action_id):
        return action_id
    digest = hashlib.sha256(
        action_id.encode("utf-8", errors="replace")
    ).hexdigest()
    return f"action-{digest[:32]}"


def input_resolution_outcome(
    command: ExecutionCommand,
    outcome: DriverOutcome,
) -> str:
    """Derive a finite public result without copying submitted values."""
    arguments = command.arguments
    if arguments.get("cancelled") is True:
        return "cancelled"
    if arguments.get("accepted") is True or arguments.get("approved") is True:
        return "accepted"
    return outcome.status.value


def serialize_driver_outcome(outcome: DriverOutcome) -> str:
    """Serialize the private replay fields of one Driver outcome."""
    return json.dumps(
        {
            "status": outcome.status.value,
            "failure": (
                {
                    "code": outcome.failure.code,
                    "retryable": outcome.failure.retryable,
                }
                if outcome.failure is not None
                else None
            ),
            "provider_revision": outcome.provider_revision,
            "retry_after_ms": outcome.retry_after_ms,
            "tracking_health": outcome.tracking_health.value,
            "cancellation_outcome": outcome.cancellation_outcome,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_driver_outcome(value: str) -> DriverOutcome:
    """Deserialize a previously persisted Driver operation outcome."""
    raw = json.loads(value)
    failure = raw.get("failure")
    if failure is not None:
        return DriverOutcome.failed(
            code=failure["code"],
            retryable=bool(failure["retryable"]),
        )
    return DriverOutcome(
        status=ExecutionStatus(raw["status"]),
        provider_revision=int(raw.get("provider_revision", 0)),
        retry_after_ms=raw.get("retry_after_ms"),
        tracking_health=TrackingHealth(
            raw.get("tracking_health", TrackingHealth.HEALTHY.value)
        ),
        cancellation_outcome=raw.get("cancellation_outcome"),
    )
