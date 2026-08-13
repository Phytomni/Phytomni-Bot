# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Research root-launch failure cleanup tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchInputFailure,
)
from mcp_server_phytomni.api import research_launch

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_launch_failure_revokes_after_durable_failure_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed admission releases grants before exposing launch failure."""
    events: list[tuple[str, str]] = []

    def mark_admission_launch_failed(run_id: str, _failure: Any) -> bool:
        """Record the durable failure boundary."""
        events.append(("marked", run_id))
        return True

    async def launcher(*_args: Any) -> None:
        """Fail after the admission transaction commits."""
        raise RuntimeError("provider unavailable")

    async def revoke(run_id: str) -> None:
        """Record immediate best-effort cleanup."""
        events.append(("revoked", run_id))

    monkeypatch.setattr(
        research_launch,
        "revoke_registered_research_run",
        revoke,
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await research_launch.launch_worker(
            launcher,
            object(),
            SimpleNamespace(worker_owner=True, run_id="run-launch"),
            object(),
            SimpleNamespace(
                mark_admission_launch_failed=mark_admission_launch_failed
            ),
        )

    assert caught.value.code == "research_input_resolution_unavailable"
    assert events == [("marked", "run-launch"), ("revoked", "run-launch")]
