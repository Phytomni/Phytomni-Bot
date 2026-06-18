# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""The analyst dispatch context routes output user-neutrally.

``prepare_analyst_dispatch_context`` derives the output directory from
the input fingerprint via the content-addressed ``shared_output_key``,
so a fingerprinted dispatch lands the same tenant-neutral path
regardless of ``config.USER_ID``. This makes deep_genome's brief_gene /
evolution / design mounts tenant-safe even though they submit under
their module-default config rather than the parent's authenticated user.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import mcp_server_phytomni.agents.shared.analysis_storage as storage_mod
from mcp_server_phytomni.agents.shared.analysis import (
    prepare_analyst_dispatch_context,
)

pytestmark = pytest.mark.unit

_FINGERPRINT = "a" * 64


def _config(user_id: str) -> Any:
    """Return a minimal public config for the dispatch context."""
    return SimpleNamespace(
        USER_ID=user_id,
        OBS_SERVER="https://obs.example",
        BUCKET_NAME="phytomni",
    )


def _sensitive() -> Any:
    """Return a sensitive config exposing OBS credentials."""
    return SimpleNamespace(obs_credentials=lambda: ("ak", "sk"))


def test_fingerprinted_output_dir_is_identical_across_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tenants with the same fingerprint land the same neutral path."""
    # Relay mode short-circuits OBS creation so create_output_dir runs
    # its real fingerprint routing without touching the network.
    monkeypatch.setattr(storage_mod, "relay_mode_enabled", lambda: True)
    request = {"analysis_type": "evolution_analysis", "target_id": "g1"}

    alice = prepare_analyst_dispatch_context(
        _config("alice"), _sensitive(), request, _FINGERPRINT
    )
    bob = prepare_analyst_dispatch_context(
        _config("bob"), _sensitive(), request, _FINGERPRINT
    )

    assert alice.output_dir == bob.output_dir
    assert _FINGERPRINT in alice.output_dir
    assert "alice" not in alice.output_dir
    assert "bob" not in alice.output_dir
    # The thread_id is the only user-scoped value and never addresses
    # stored results, so the per-tenant divergence there is benign.
    assert alice.thread_id != bob.thread_id
