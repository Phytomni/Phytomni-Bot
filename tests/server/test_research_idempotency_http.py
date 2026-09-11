# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP-facing status projection tests for Research admission."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.test_research_admission import _request, _store

from mcp_server_phytomni.api.research_input import admit_research_request
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def test_replay_projects_202_while_running_and_200_after_terminal(
    tmp_path: Path,
) -> None:
    """A canonical replay follows the public run lifecycle status."""
    store, database = _store(tmp_path)
    first = admit_research_request(_request("http-key"), store)
    assert first.status_code == 202

    with closed_sqlite_connection(database) as connection:
        connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE run_id = ?",
            (first.run_id,),
        )

    replay = admit_research_request(_request("http-key"), store)
    assert replay.run_id == first.run_id
    assert replay.status_code == 200
    assert replay.worker_owner is False
    assert RunRegistry(database).get_run(first.run_id, owner="owner-1")
