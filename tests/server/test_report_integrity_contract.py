# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Producer golden for failed Deep Genome finalization with usable science."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.test_deep_genome_store_transitions import _completed_store

from mcp_server_phytomni.runtime.deep_genome_store_projection import (
    snapshot_to_canonical_result,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "report-integrity"
    / "deep-genome-failed-finalization.json"
)


def failed_finalization_contract(tmp_path: Path) -> dict[str, Any]:
    """Build the actual canonical producer result from synthetic local rows."""
    store, reservation, _ = _completed_store(tmp_path)
    with closed_sqlite_connection(store.db_path) as connection:
        connection.execute(
            "UPDATE tasks SET report_updated_at = ? WHERE task_id = ?",
            ("2026-09-12T00:00:00+00:00", reservation.umbrella_task_id),
        )
        connection.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = ?",
            (
                json.dumps(
                    {
                        "formatted": {
                            "references": [
                                {
                                    "file_id": "unused",
                                    "title": "Unused synthetic reference",
                                },
                                {
                                    "file_id": "evidence",
                                    "title": "Synthetic evidence",
                                },
                            ]
                        }
                    }
                ),
                reservation.run_id,
            ),
        )
    snapshot = store.fail_umbrella(
        reservation.umbrella_task_id, reason="final synthesis failed"
    )
    record = RunRegistry(store.db_path).get_run(
        reservation.run_id, owner="alice"
    )
    assert record is not None
    assert record.status == "failed"
    return snapshot_to_canonical_result(
        snapshot, existing_result=record.result
    )


def test_failed_finalization_matches_report_integrity_golden(
    tmp_path: Path,
) -> None:
    """Producer bytes and semantic assertions pin the consumer contract."""
    result = failed_finalization_contract(tmp_path)
    expected_bytes = _FIXTURE.read_bytes()
    assert (
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    ).encode() == expected_bytes
    assert result == json.loads(expected_bytes)
    assert "<sup>1</sup>" in result["formatted"]["answer"]
    assert "Unavailable:" not in result["formatted"]["answer"]
    assert result["formatted"]["references"][0]["file_id"] == "evidence"
    assert result["execution"]["report"] == {
        "state": "intermediate",
        "degraded": True,
        "source_artifact_count": 0,
    }
    assert result["execution"]["tasks"] == [
        {"id": "task-1", "accepted": True, "status": "failed"}
    ]
    progress = result["formatted"]["metadata"]["deep_genome"]["progress"]
    assert progress["total"] == progress["succeeded"] == 12
    assert progress["failed"] == 0
    assert result["formatted"]["metadata"]["deep_genome"]["failure_count"] == 0
    assert "deep_genome_report_degraded" in {
        warning["code"] for warning in result["execution"]["warnings"]
    }
