# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Recorder kind, doomed-child, and GetRun five-key merge pins."""

from __future__ import annotations

import json

import pytest

from mcp_server_phytomni.api.lifecycle_contract import canonicalize_run_record
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from mcp_server_phytomni.runtime.submit_recorder import record_submitted_task

pytestmark = pytest.mark.server


def test_reserved_submissions_include_kind_and_error_code(
    tasks_db_path: str,
) -> None:
    """A Design envelope stores per-child kind and a bounded error code."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-kind",
            user_id="alice",
            agent="design",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-kind"),
        result=empty_execution_projection(),
    )

    with request_context("alice", "req-kind", "run-kind"):
        record_submitted_task(
            {
                "design_task_result": [
                    {
                        "task_id": "design-protein",
                        "output_dir": "/safe/protein",
                        "analysis_type": "protein_structure_analysis",
                    },
                    {
                        "task_id": "design-promoter",
                        "output_dir": "/safe/promoter",
                        "analysis_type": "promoter_analysis",
                        "accepted": False,
                        "status": "failed",
                        "error_code": "input_rejected",
                    },
                ]
            },
            agent="design",
        )

    stored = registry.get_run("run-kind", owner="alice")
    assert stored is not None
    result = stored.result
    assert result is not None
    tasks = result["execution"]["tasks"]
    assert tasks[0]["kind"] == "protein_structure_analysis"
    assert tasks[0]["accepted"] is True
    assert tasks[0]["error_code"] is None
    assert tasks[1]["kind"] == "promoter_analysis"
    assert tasks[1]["accepted"] is False
    assert tasks[1]["status"] == "failed"
    assert tasks[1]["error_code"] == "input_rejected"
    assert "Traceback" not in json.dumps(result)
    assert stored.task_ids == ("design-protein",)


def test_one_rejection_records_one_doomed_child(
    tasks_db_path: str,
) -> None:
    """Nested doomed rows must not be duplicated from submission_rejections."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-one-doomed",
            user_id="alice",
            agent="design",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-one-doomed"),
        result=empty_execution_projection(),
    )
    with request_context("alice", "req-one-doomed", "run-one-doomed"):
        record_submitted_task(
            {
                "design_task_result": [
                    {
                        "task_id": "rejected-protein_structure_analysis",
                        "accepted": False,
                        "status": "failed",
                        "analysis_type": "protein_structure_analysis",
                        "error_code": "input_rejected",
                    }
                ],
                "phytomni_state": {
                    "submission_rejections": [
                        {"goal": "AT1G01010", "code": "input_rejected"}
                    ]
                },
            },
            agent="design",
        )

    stored = registry.get_run("run-one-doomed", owner="alice")
    assert stored is not None
    result = stored.result
    assert result is not None
    tasks = result["execution"]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "rejected-protein_structure_analysis"
    assert tasks[0]["accepted"] is False
    assert tasks[0]["kind"] == "protein_structure_analysis"
    assert tasks[0]["error_code"] == "input_rejected"
    assert not stored.task_ids


def test_running_result_update_then_canonicalize_keeps_kind(
    tasks_db_path: str,
) -> None:
    """GetRun still has five-key rows after a formatted worker update."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-kind-update",
            user_id="alice",
            agent="design",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-kind-update"),
        result=empty_execution_projection(),
    )
    envelope = {
        "design_task_result": [
            {
                "task_id": "design-protein",
                "output_dir": "/safe/protein",
                "analysis_type": "protein_structure_analysis",
            },
            {
                "task_id": "design-promoter",
                "output_dir": "/safe/promoter",
                "analysis_type": "promoter_analysis",
                "accepted": False,
                "status": "failed",
                "error_code": "input_rejected",
            },
        ]
    }
    with request_context("alice", "req-kind-update", "run-kind-update"):
        record_submitted_task(envelope, agent="design")

    thin = empty_execution_projection()
    thin["execution"]["tasks"] = [{"id": "design-protein", "accepted": True}]
    assert registry.update_running_result(
        "run-kind-update",
        owner="alice",
        result=thin,
    )
    stored = registry.get_run("run-kind-update", owner="alice")
    assert stored is not None
    canonical = canonicalize_run_record(
        {
            "run_id": "run-kind-update",
            "agent": "design",
            "status": stored.status,
            "task_ids": list(stored.task_ids),
            "result": stored.result,
        }
    )
    tasks = canonical["result"]["execution"]["tasks"]
    assert tasks[0]["id"] == "design-protein"
    assert tasks[0]["accepted"] is True
    assert tasks[0]["kind"] == "protein_structure_analysis"
    assert tasks[0]["error_code"] is None
    assert tasks[1]["id"] == "design-promoter"
    assert tasks[1]["accepted"] is False
    assert tasks[1]["status"] == "failed"
    assert tasks[1]["kind"] == "promoter_analysis"
    assert tasks[1]["error_code"] == "input_rejected"
