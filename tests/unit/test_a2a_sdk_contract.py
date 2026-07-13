# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for the supported A2A Python SDK v1 window."""

from __future__ import annotations

import inspect
from importlib.metadata import version

import pytest
from a2a import types as a2a_types
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types import (
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    Part,
    Role,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.proto_utils import to_stream_response
from google.protobuf import json_format

pytestmark = pytest.mark.unit


def test_a2a_sdk_stays_inside_the_supported_v1_window() -> None:
    """The installed SDK must satisfy the direct dependency contract."""
    major, minor, *_ = version("a2a-sdk").split(".")

    assert int(major) == 1
    assert int(minor) >= 1


def test_a2a_v1_card_and_client_interfaces_remain_public() -> None:
    """Discovery keeps the SDK card/client entry points in its v1 window."""
    assert list(AgentCard.DESCRIPTOR.fields_by_name) == [
        "name",
        "description",
        "supported_interfaces",
        "provider",
        "version",
        "documentation_url",
        "capabilities",
        "security_schemes",
        "security_requirements",
        "default_input_modes",
        "default_output_modes",
        "skills",
        "signatures",
        "icon_url",
    ]
    assert list(AgentInterface.DESCRIPTOR.fields_by_name) == [
        "url",
        "protocol_binding",
        "tenant",
        "protocol_version",
    ]
    assert list(AgentSkill.DESCRIPTOR.fields_by_name) == [
        "id",
        "name",
        "description",
        "tags",
        "examples",
        "input_modes",
        "output_modes",
        "security_requirements",
    ]
    assert list(inspect.signature(ClientConfig).parameters) == [
        "streaming",
        "polling",
        "httpx_client",
        "grpc_channel_factory",
        "supported_protocol_bindings",
        "use_client_preference",
        "accepted_output_modes",
        "push_notification_config",
    ]
    assert list(inspect.signature(ClientFactory).parameters) == ["config"]
    assert list(inspect.signature(ClientFactory.create).parameters) == [
        "self",
        "card",
        "interceptors",
    ]
    assert list(
        inspect.signature(ClientFactory.create_from_url).parameters
    ) == [
        "self",
        "url",
        "interceptors",
        "relative_card_path",
        "resolver_http_kwargs",
        "signature_verifier",
    ]
    assert list(inspect.signature(A2ACardResolver).parameters) == [
        "httpx_client",
        "base_url",
        "agent_card_path",
    ]
    assert list(inspect.signature(parse_agent_card).parameters) == [
        "agent_card_data"
    ]


def test_a2a_v1_role_and_task_state_enums_remain_stable() -> None:
    """Protocol lifecycle and role enums retain their v1 wire names."""
    assert TaskState.keys() == [
        "TASK_STATE_UNSPECIFIED",
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_AUTH_REQUIRED",
    ]
    assert Role.keys() == [
        "ROLE_UNSPECIFIED",
        "ROLE_USER",
        "ROLE_AGENT",
    ]


def test_a2a_v1_part_and_stream_payload_oneofs_remain_stable() -> None:
    """The protobuf oneofs replace the legacy typed-part wrappers."""
    part_fields = Part.DESCRIPTOR.oneofs_by_name["content"].fields
    stream_fields = StreamResponse.DESCRIPTOR.oneofs_by_name["payload"].fields

    assert [field.name for field in part_fields] == [
        "text",
        "raw",
        "url",
        "data",
    ]
    assert [field.name for field in stream_fields] == [
        "task",
        "message",
        "status_update",
        "artifact_update",
    ]
    assert not hasattr(a2a_types, "TextPart")
    assert not hasattr(a2a_types, "DataPart")


def test_a2a_v1_stream_event_wrappers_serialize_consistently() -> None:
    """Status and artifact events keep their protobuf JSON wrapper shape."""
    status_event = TaskStatusUpdateEvent(
        task_id="task-contract",
        context_id="context-contract",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    artifact_event = TaskArtifactUpdateEvent(
        task_id="task-contract",
        context_id="context-contract",
        artifact=Artifact(
            artifact_id="artifact-contract",
            parts=[Part(text="result", media_type="text/plain")],
        ),
        last_chunk=True,
    )

    status_wrapper = to_stream_response(status_event)
    artifact_wrapper = to_stream_response(artifact_event)

    assert status_wrapper.WhichOneof("payload") == "status_update"
    assert artifact_wrapper.WhichOneof("payload") == "artifact_update"
    assert json_format.MessageToDict(status_wrapper) == {
        "statusUpdate": {
            "taskId": "task-contract",
            "contextId": "context-contract",
            "status": {"state": "TASK_STATE_WORKING"},
        }
    }
    assert json_format.MessageToDict(artifact_wrapper) == {
        "artifactUpdate": {
            "taskId": "task-contract",
            "contextId": "context-contract",
            "artifact": {
                "artifactId": "artifact-contract",
                "parts": [{"text": "result", "mediaType": "text/plain"}],
            },
            "lastChunk": True,
        },
    }
