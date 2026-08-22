# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""The analyst dispatch seam routes output to the tenant-neutral key.

``prepare_analyst_dispatch_context`` routes the output directory to the
content-addressed ``shared_output_key`` whenever a fingerprint is
supplied -- overriding any preset, user-scoped ``output_dir`` -- so a
dispatch lands the same tenant-neutral path regardless of
``config.USER_ID``. This is what makes deep_genome's evolution / design
mounts tenant-safe even when the graph presets a user-scoped dir.
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


async def test_fingerprinted_output_dir_is_identical_across_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tenants with the same fingerprint land the same neutral path."""
    # Relay mode short-circuits OBS creation so create_output_dir runs
    # its real fingerprint routing without touching the network.
    monkeypatch.setattr(storage_mod, "relay_mode_enabled", lambda: True)
    request = {"analysis_type": "evolution_analysis", "target_id": "g1"}

    alice = await prepare_analyst_dispatch_context(
        _config("alice"), request, _FINGERPRINT
    )
    bob = await prepare_analyst_dispatch_context(
        _config("bob"), request, _FINGERPRINT
    )

    assert alice.output_dir == bob.output_dir
    assert _FINGERPRINT in alice.output_dir
    assert "alice" not in alice.output_dir
    assert "bob" not in alice.output_dir
    # The thread_id is the only user-scoped value and never addresses
    # stored results, so the per-tenant divergence there is benign.
    assert alice.thread_id != bob.thread_id


async def test_preset_output_dir_is_overridden_by_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preset user-scoped request output_dir is overridden by the fp.

    The real evolution / generic deep_genome mount path presets a
    user-scoped ``output_dir`` (e.g. ``user_data/anonymous/...``); the
    dispatch seam's fingerprint must win so results still land at the
    tenant-neutral shared key rather than the preset user path.
    """
    monkeypatch.setattr(storage_mod, "relay_mode_enabled", lambda: True)
    preset = "/obs/phytomni/agent_data/user_data/anonymous/runs/r1/output"
    request = {
        "analysis_type": "evolution_analysis",
        "target_id": "g1",
        "output_dir": preset,
    }

    ctx = await prepare_analyst_dispatch_context(
        _config("anonymous"), request, _FINGERPRINT
    )

    assert ctx.output_dir != preset
    assert _FINGERPRINT in ctx.output_dir
    assert "user_data" not in ctx.output_dir
    assert "anonymous" not in ctx.output_dir


async def test_flagged_dump_child_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network-style flagged children under the config dump allocate a root."""
    captured: dict[str, Any] = {}

    async def fake_create(*_args: Any, **_kwargs: Any) -> str:
        captured["called"] = True
        return "/obs/phytomni/agent_data/users/alice/run-new"

    monkeypatch.setattr(storage_mod, "create_output_dir", fake_create)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis.create_output_dir",
        fake_create,
    )
    default = "/obs/phytomni/agent_data/test/output"
    ctx = await prepare_analyst_dispatch_context(
        SimpleNamespace(
            USER_ID="alice",
            OBS_SERVER="https://obs.example",
            BUCKET_NAME="phytomni",
            OUTPUT_DIR=default,
        ),
        {
            "analysis_type": "gene_network_analysis",
            "target_id": "TO:0000014",
            "output_dir": f"{default}/children/part-001",
            "output_dir_is_result_child": True,
        },
    )

    assert captured.get("called") is True
    assert ctx.output_dir == (
        "/obs/phytomni/agent_data/users/alice/run-new/children/part-001"
    )
    assert not ctx.output_dir.startswith(default)
