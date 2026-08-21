# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct-outcome tests for local background submissions."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.asyncio_helpers import wait_until

from mcp_server_phytomni.runtime.background_submission import (
    BackgroundSubmissionOutcome,
    launch_background_submission,
    reserve_background_submission,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.live_tasks import is_live_running
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
)

pytestmark = pytest.mark.unit


async def test_local_worker_outcome_settles_reserved_run_succeeded(
    tmp_path: Path,
) -> None:
    """A local blocking worker settles its preallocated run directly."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="data",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-local-data"),
        db_path=db_path,
    )
    result = empty_execution_projection()
    result["formatted"] = {"answer": "finished table"}

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome(status="succeeded", result=result)

    launch_background_submission(reservation, operation, db_path=db_path)
    await wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == result
