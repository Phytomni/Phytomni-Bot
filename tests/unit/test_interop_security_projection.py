# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Security regression tests for interop state and credential boundaries."""

from __future__ import annotations

import json
import operator
from collections.abc import MutableMapping
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import SecretStr

from mcp_server_phytomni.agents.shared.interop import (
    initial_interop_state,
    interop_evidence_update,
    interop_target_descriptor,
    project_a2a_evidence,
)
from mcp_server_phytomni.interop.credentials import (
    credential_headers,
    credential_references,
)
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit


def test_credential_reference_surface_is_read_only() -> None:
    """Only the transport boundary receives a read-only header mapping."""
    raw = SecretStr(
        json.dumps(
            {
                "peer-auth": {
                    "headers": {
                        "Authorization": "Bearer operator-secret",
                    }
                }
            }
        )
    )

    assert credential_references(raw) == frozenset({"peer-auth"})
    assert "operator-secret" not in repr(credential_references(raw))
    headers = credential_headers(raw, "peer-auth")
    assert headers["Authorization"] == "Bearer operator-secret"
    with pytest.raises(TypeError):
        operator.setitem(
            cast(MutableMapping[str, str], headers),
            "Authorization",
            "caller-value",
        )


def test_untrusted_evidence_labels_and_content_are_redacted_before_state() -> (
    None
):
    """Endpoint/token-shaped peer data cannot enter graph state metadata."""
    evidence = {
        "target_id": "https://attacker.example.test/?token=hidden",
        "kind": "a2a",
        "capability": "Authorization: Bearer hidden",
        "content": (
            "peer response https://attacker.example.test/private "
            "token=hidden"
        ),
        "truncated": False,
    }

    projected = project_a2a_evidence(evidence)
    state_update = interop_evidence_update(
        evidence,
        status="failed",
        latency_seconds=0.01,
    )

    assert projected["target_id"] == "unknown"
    assert projected["capability"] == "unknown"
    assert "attacker.example.test" not in projected["content"]
    assert "hidden" not in projected["content"]
    assert state_update["interop"][0]["target_id"] == "unknown"
    assert state_update["interop"][0]["capability"] == "unknown"


def test_initial_state_filters_caller_supplied_endpoint_shaped_targets() -> (
    None
):
    """Raw request options cannot be copied into persisted graph state."""
    state = initial_interop_state(
        {
            "interop_mode": "auto",
            "interop_targets": [
                "peer-a",
                "https://attacker.example.test/?token=hidden",
                "peer_b",
                "../escape",
            ],
        }
    )

    assert state["interop_targets"] == ["peer-a", "peer_b"]
    assert "attacker.example.test" not in repr(state)
    assert "hidden" not in repr(state)


def test_unknown_registry_fallback_does_not_echo_untrusted_target_id() -> None:
    """A failed lookup records ``unknown`` instead of the caller's URL."""
    dependencies = SimpleNamespace(
        registry=InteropRegistry(_targets={})
    )

    target_id, capability = interop_target_descriptor(
        dependencies,
        ["https://attacker.example.test/?token=hidden"],
        kind="a2a",
        capability="annotate",
    )

    assert target_id == "unknown"
    assert capability == "annotate"
