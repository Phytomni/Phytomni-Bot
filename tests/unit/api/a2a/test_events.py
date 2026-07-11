# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for canonical A2A v1 streaming event wrappers."""

from __future__ import annotations

import pytest
from a2a.types import Artifact, Part, TaskState
from google.protobuf import json_format

from mcp_server_phytomni.api.a2a.events import (
    ArtifactUpdateOptions,
    build_artifact_update,
    build_status_update,
)

pytestmark = pytest.mark.unit


def test_status_update_wrapper_matches_v1_json_golden() -> None:
    """Status updates use the ``statusUpdate`` StreamResponse payload."""
    response = build_status_update(
        "task-contract",
        "context-contract",
        TaskState.TASK_STATE_WORKING,
        metadata={"phase": "retrieval", "current": 2, "total": 4},
    )

    assert response.WhichOneof("payload") == "status_update"
    assert json_format.MessageToDict(response) == {
        "statusUpdate": {
            "taskId": "task-contract",
            "contextId": "context-contract",
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"phase": "retrieval", "current": 2.0, "total": 4.0},
        }
    }


def test_artifact_update_wrapper_matches_v1_json_golden() -> None:
    """Artifact updates retain append and terminal-chunk markers."""
    response = build_artifact_update(
        "task-contract",
        "context-contract",
        Artifact(
            artifact_id="artifact-contract",
            parts=[Part(text="result", media_type="text/plain")],
        ),
        options=ArtifactUpdateOptions(
            append=True,
            last_chunk=True,
            metadata={"section": "answer"},
        ),
    )

    assert response.WhichOneof("payload") == "artifact_update"
    assert json_format.MessageToDict(response) == {
        "artifactUpdate": {
            "taskId": "task-contract",
            "contextId": "context-contract",
            "artifact": {
                "artifactId": "artifact-contract",
                "parts": [{"text": "result", "mediaType": "text/plain"}],
            },
            "append": True,
            "lastChunk": True,
            "metadata": {"section": "answer"},
        }
    }


def test_status_update_accepts_named_task_state() -> None:
    """Named protobuf enum values serialize exactly like integer enums."""
    response = build_status_update(
        "task-contract", "context-contract", "TASK_STATE_COMPLETED"
    )

    assert (
        response.status_update.status.state == TaskState.TASK_STATE_COMPLETED
    )
