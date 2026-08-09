# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Focused validation tests for Research outbox storage helpers."""

from __future__ import annotations

import pytest
from tests.unit.test_research_dispatch_outbox import _digest as _outbox_digest

from mcp_server_phytomni.agents.research import dispatch_outbox_storage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("payload", "digest", "output_dir", "fingerprint", "expected"),
    [
        ({}, "", "out", "fingerprint", True),
        (None, "a" * 64, "out", "fingerprint", False),
        ({}, "a" * 64, "out", "fingerprint", False),
        (
            {"output_dir": "wrong", "dispatch_fingerprint": "fingerprint"},
            _outbox_digest(
                {"output_dir": "wrong", "dispatch_fingerprint": "fingerprint"}
            ),
            "out",
            "fingerprint",
            False,
        ),
    ],
)
def test_outbox_storage_rejects_tampered_payload_bindings(
    payload: object,
    digest: object,
    output_dir: object,
    fingerprint: object,
    expected: bool,
) -> None:
    """Payload digests bind the exact durable output and dispatch identity."""
    assert (
        dispatch_outbox_storage.payload_is_consistent(
            payload, digest, output_dir, fingerprint
        )
        is expected
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("remote-1", "remote-1"),
        ({"remote_task_id": "remote-2"}, "remote-2"),
        ({"task_id": "failed-1", "status": "failed"}, None),
        ({"id": ""}, None),
    ],
)
def test_outbox_storage_classifies_provider_task_id_responses(
    response: object, expected: str | None
) -> None:
    """Only non-terminal bounded task identities are reusable."""
    assert dispatch_outbox_storage.reusable_task_id(response) == expected
