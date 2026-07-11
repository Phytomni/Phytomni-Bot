# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Construct canonical A2A v1 streaming event wrappers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from a2a.types import (
    Artifact,
    Message,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.proto_utils import to_stream_response
from google.protobuf import json_format

__all__ = [
    "ArtifactUpdateOptions",
    "build_artifact_update",
    "build_status_update",
]


@dataclass(frozen=True)
class ArtifactUpdateOptions:
    """Optional flags and metadata for one artifact update event."""

    append: bool = False
    last_chunk: bool = False
    metadata: Mapping[str, Any] | None = None


def _parse_metadata(
    target: TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    metadata: Mapping[str, Any] | None,
) -> None:
    """Copy JSON-compatible metadata into a protobuf ``Struct``."""
    if metadata:
        json_format.ParseDict(dict(metadata), target.metadata)


def build_status_update(
    task_id: str,
    context_id: str,
    state: TaskState | str,
    *,
    message: Message | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> StreamResponse:
    """Build a ``StreamResponse`` carrying one status update event."""
    event = TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state, message=message),
    )
    _parse_metadata(event, metadata)
    return to_stream_response(event)


def build_artifact_update(
    task_id: str,
    context_id: str,
    artifact: Artifact,
    *,
    options: ArtifactUpdateOptions | None = None,
) -> StreamResponse:
    """Build a ``StreamResponse`` carrying one artifact update event."""
    update_options = options or ArtifactUpdateOptions()
    event = TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        artifact=artifact,
        append=update_options.append,
        last_chunk=update_options.last_chunk,
    )
    _parse_metadata(event, update_options.metadata)
    return to_stream_response(event)
