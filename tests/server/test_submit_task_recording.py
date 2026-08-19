# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for submit-handler unified run+task registry recording.

Pin the chokepoint: ``records_submission(agent)`` forwards a submit
handler's result unchanged while persisting both the child task row
and an owning ``runs`` row (``origin="remote"``), the recorder is
best-effort on malformed results, and all five submit-style handlers
are decorated with their canonical agent slug.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.api.lifecycle_contract import canonicalize_run_record
from mcp_server_phytomni.mcp.handlers import (
    handle_analyst_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_in_silico_research_agent,
)
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.request_context import (
    current_accepted_task_ids,
    current_recorder_degraded,
    current_run_id,
    request_context,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from mcp_server_phytomni.runtime.submit_recorder import (
    record_submitted_task,
    records_submission,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.server


async def test_decorator_records_task_run_and_passes_result_through(
    tasks_db_path: str,
) -> None:
    """Verify the wrapper logs the run + task yet returns result as-is.

    Args:
        tasks_db_path: Temp registry DB fixture.
    """
    submitted = {"task_id": "T-1", "output_dir": "/obs/run"}

    async def fake_handler(args: Any) -> Any:
        """Return a canned submission result.

        Args:
            args: Ignored tool-argument model.

        Returns:
            The canned submission dict.
        """
        _ = args
        return submitted

    wrapped = records_submission("analyst")(fake_handler)

    result = await wrapped(object())

    assert result is submitted
    assert TaskManager(tasks_db_path).get_task("T-1") == {
        "task_id": "T-1",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/run",
        "source_task_id": None,
    }
    with closed_sqlite_connection(tasks_db_path) as conn:
        row = conn.execute(
            "SELECT run_id, user_id, agent, origin FROM tasks "
            "WHERE task_id = ?",
            ("T-1",),
        ).fetchone()
    assert row is not None
    run_id, user_id, agent, origin = row
    assert run_id
    assert user_id == "anonymous"
    assert agent == "analyst"
    assert origin == "remote"
    listing = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(listing) == 1
    assert listing[0].spec.run_id == run_id
    assert listing[0].spec.agent == "analyst"
    assert listing[0].spec.origin == "remote"
    assert listing[0].status == "running"
    assert listing[0].task_ids == ("T-1",)
    # Submit-time write seeds the canonical envelope so a client polling
    # /v1/runs/{id} during running sees the same field ownership as terminal.
    expected = empty_execution_projection(result_archive_required=True)
    expected["execution"]["tasks"] = [
        {
            "id": "T-1",
            "accepted": True,
            "status": "submitted",
            "kind": "analyst",
            "error_code": None,
        }
    ]
    expected["execution"]["output_dirs"] = ["/obs/run"]
    assert listing[0].result == expected


def test_record_upsert_preserves_prior_fingerprint(
    tasks_db_path: str,
) -> None:
    """A later run-linkage write must not erase an earlier fingerprint.

    The dispatch seam writes ``(task_id, fingerprint)`` with NULL run
    columns; the per-tool recorder later writes the same ``task_id``
    with a populated run context but NO fingerprint. The COALESCE upsert
    keeps the seam's fingerprint while still applying the recorder's run
    id.

    Args:
        tasks_db_path: Temp registry DB fixture.
    """
    mgr = TaskManager(tasks_db_path)
    # Seam-style write: fingerprint set, run columns NULL.
    mgr.record(
        Submission(
            task_id="T-up",
            status="submitted",
            output_dir="/obs/up",
            input_fingerprint="fp-keep",
        )
    )
    # Recorder-style write: run context set, fingerprint absent.
    mgr.record(
        Submission(
            task_id="T-up",
            status="submitted",
            output_dir="/obs/up",
            run_context=RunContext(
                run_id="R-1",
                user_id="anonymous",
                agent="design",
                origin="remote",
                created_at="2026-06-11T00:00:00+00:00",
                updated_at="2026-06-11T00:00:00+00:00",
            ),
        )
    )

    assert mgr.get_task_by_fingerprint("fp-keep") == {
        "task_id": "T-up",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/up",
        "source_task_id": None,
    }
    with closed_sqlite_connection(tasks_db_path) as conn:
        run_id = conn.execute(
            "SELECT run_id FROM tasks WHERE task_id = ?", ("T-up",)
        ).fetchone()[0]
    assert run_id == "R-1"


def testrecord_submitted_task_ignores_malformed_results(
    tasks_db_path: str,
) -> None:
    """Verify non-dict / missing-id results record nothing, no raise.

    Args:
        tasks_db_path: Temp registry DB fixture.
    """
    record_submitted_task("not a dict", agent="analyst")
    record_submitted_task({}, agent="analyst")
    record_submitted_task({"task_id": ""}, agent="analyst")

    assert TaskManager(tasks_db_path).get_task("") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")


def test_all_submit_handlers_are_decorated() -> None:
    """Verify every submit-style handler carries the recorder wrapper.

    ``functools.wraps`` inside the decorator factory keeps the original
    handler reachable via ``__wrapped__`` even when the factory itself
    is parameterised with a static agent slug.
    """
    for handler in (
        handle_analyst_agent,
        handle_deep_genome_agent,
        handle_digital_design_agent,
        handle_gene_network_agent,
        handle_in_silico_research_agent,
    ):
        assert hasattr(handler, "__wrapped__"), handler.__name__


def test_record_binds_run_id_contextvar(tasks_db_path: str) -> None:
    """The chokepoint binds ``current_run_id`` to the freshly minted id.

    Pin the audit-1.2 contract: after a successful chokepoint write,
    the HTTP layer can recover the run id directly from the
    contextvar without re-reading a formatter-specific metadata key.
    """
    assert current_run_id() is None
    record_submitted_task(
        {"task_id": "T-bind", "output_dir": "/obs/run"},
        agent="analyst",
    )
    bound = current_run_id()
    assert bound is not None
    assert bound.endswith("analyst") or "analyst" in bound
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.run_id == bound


def test_record_persists_request_id_across_run_reads(
    tasks_db_path: str,
) -> None:
    """Remote Analyst identity keeps the request id beside run and task ids."""
    with request_context("analyst-owner", "request-analyst-1"):
        record_submitted_task(
            {"task_id": "T-request", "output_dir": "/obs/run"},
            agent="analyst",
        )

    registry = RunRegistry(tasks_db_path)
    listed = registry.list_runs(owner="analyst-owner")
    assert len(listed) == 1
    run_id = listed[0].spec.run_id
    assert listed[0].request_info.request_id == "request-analyst-1"
    fetched = registry.get_run(run_id, owner="analyst-owner")
    assert fetched is not None
    assert fetched.task_ids == ("T-request",)
    assert fetched.request_info.request_id == "request-analyst-1"


@pytest.mark.parametrize(
    ("agent", "result", "expected_ids"),
    [
        (
            "analyst",
            {"task_id": "analyst-1", "output_dir": "/safe/a"},
            ("analyst-1",),
        ),
        (
            "research",
            {
                "task_ids": ["research-1", "research-2"],
                "output_dir": "/safe/r",
            },
            ("research-1", "research-2"),
        ),
        (
            "network",
            {
                "network_task": {
                    "task_id": "network-1",
                    "output_dir": "/safe/n",
                }
            },
            ("network-1",),
        ),
        (
            "design",
            {
                "design_task_result": [
                    {"task_id": "design-1", "output_dir": "/safe/d1"},
                    {"task_id": "design-2", "output_dir": "/safe/d2"},
                ]
            },
            ("design-1", "design-2"),
        ),
    ],
)
def test_recorder_attaches_children_to_reserved_run(
    tasks_db_path: str,
    agent: str,
    result: dict[str, Any],
    expected_ids: tuple[str, ...],
) -> None:
    """Attach each accepted child to the already-reserved umbrella."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-reserved",
            user_id="alice",
            agent=agent,
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-1"),
        result=empty_execution_projection(),
    )

    with request_context("alice", "req-1", "run-reserved"):
        record_submitted_task(result, agent=agent)
        assert current_run_id() == "run-reserved"
        assert current_accepted_task_ids() == expected_ids

    stored = registry.get_run("run-reserved", owner="alice")
    assert stored is not None
    assert tuple(stored.task_ids) == expected_ids
    assert stored.result is not None
    assert stored.result["execution"]["tasks"] == [
        {
            "id": task_id,
            "accepted": True,
            "status": "submitted",
            "kind": agent,
            "error_code": None,
        }
        for task_id in expected_ids
    ]
    if agent == "design":
        expected_output_dirs = [
            str(entry.get("output_dir") or "")
            for entry in result["design_task_result"]
        ]
    elif agent == "network":
        expected_output_dirs = [
            str(result["network_task"].get("output_dir") or "")
        ]
    else:
        expected_output_dirs = [str(result.get("output_dir") or "")] * len(
            expected_ids
        )
    assert stored.result["execution"]["output_dirs"] == expected_output_dirs
    with closed_sqlite_connection(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, run_id, user_id, agent FROM tasks "
            "WHERE run_id = ? ORDER BY task_id",
            ("run-reserved",),
        ).fetchall()
    assert {row[0] for row in rows} == set(expected_ids)
    assert {(row[1], row[2], row[3]) for row in rows} == {
        ("run-reserved", "alice", agent)
    }
    assert len(registry.list_runs(owner="alice", limit=10, offset=0)) == 1


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
    assert stored.task_ids == ()


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


def test_recorder_rejects_reserved_run_agent_mismatch(
    tasks_db_path: str,
) -> None:
    """Reject a reserved umbrella whose canonical agent does not match."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-agent-mismatch",
            user_id="alice",
            agent="analyst",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-mismatch"),
        result=empty_execution_projection(),
    )

    with request_context("alice", "req-mismatch", "run-agent-mismatch"):
        record_submitted_task(
            {
                "design_task_result": [
                    {
                        "task_id": "design-mismatch",
                        "output_dir": "/safe/design",
                    }
                ]
            },
            agent="design",
        )
        assert current_recorder_degraded() is True
        assert current_accepted_task_ids() == ("design-mismatch",)

    stored = registry.get_run("run-agent-mismatch", owner="alice")
    assert stored is not None
    assert stored.spec.agent == "analyst"
    assert not stored.task_ids
    assert len(registry.list_runs(owner="alice", limit=10, offset=0)) == 1


@pytest.mark.parametrize(
    ("agent", "result", "task_id"),
    [
        (
            "network",
            {
                "network_task": {
                    "task_id": "network-prefingerprinted",
                    "output_dir": "/safe/network",
                }
            },
            "network-prefingerprinted",
        ),
        (
            "design",
            {
                "design_task_result": [
                    {
                        "task_id": "design-prefingerprinted",
                        "output_dir": "/safe/design",
                    }
                ]
            },
            "design-prefingerprinted",
        ),
    ],
)
def test_recorder_claims_unowned_prefingerprint_row_for_reserved_run(
    tasks_db_path: str,
    agent: str,
    result: dict[str, Any],
    task_id: str,
) -> None:
    """Attach the dispatch seam's unclaimed task row to its umbrella run."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id=f"run-{agent}-reserved",
            user_id="alice",
            agent=agent,
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id=f"req-{agent}"),
        result=empty_execution_projection(),
    )
    TaskManager(tasks_db_path).record(
        Submission(
            task_id=task_id,
            status="submitted",
            output_dir=f"/safe/{agent}",
            input_fingerprint=f"fingerprint-{agent}",
            source_task_id=f"remote-{agent}",
        )
    )

    with request_context("alice", f"req-{agent}", f"run-{agent}-reserved"):
        record_submitted_task(result, agent=agent)
        assert current_recorder_degraded() is False

    stored = registry.get_run(f"run-{agent}-reserved", owner="alice")
    assert stored is not None
    assert stored.task_ids == (task_id,)
    with closed_sqlite_connection(tasks_db_path) as conn:
        identity = conn.execute(
            "SELECT run_id, user_id, agent, input_fingerprint, "
            "source_task_id FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    assert identity == (
        f"run-{agent}-reserved",
        "alice",
        agent,
        f"fingerprint-{agent}",
        f"remote-{agent}",
    )


def test_recorder_rejects_reserved_run_owner_mismatch(
    tasks_db_path: str,
) -> None:
    """Do not attach children when the reserved owner is different."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-owner-mismatch",
            user_id="alice",
            agent="analyst",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-owner-mismatch"),
        result=empty_execution_projection(),
    )

    with request_context("bob", "req-owner-mismatch", "run-owner-mismatch"):
        record_submitted_task(
            {"task_id": "owner-mismatch", "output_dir": "/safe/owner"},
            agent="analyst",
        )
        assert current_recorder_degraded() is True
        assert current_accepted_task_ids() == ("owner-mismatch",)

    stored = registry.get_run("run-owner-mismatch", owner="alice")
    assert stored is not None
    assert not stored.task_ids


def test_recorder_rejects_terminal_reserved_run(
    tasks_db_path: str,
) -> None:
    """Do not attach children after the umbrella has become terminal."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-terminal-mismatch",
            user_id="alice",
            agent="analyst",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-terminal-mismatch"),
        result=empty_execution_projection(),
    )
    assert registry.update_running_result(
        "run-terminal-mismatch",
        owner="alice",
        result={"terminal": True},
    )
    with closed_sqlite_connection(tasks_db_path) as conn:
        conn.execute(
            "UPDATE runs SET status = 'succeeded' WHERE run_id = ?",
            ("run-terminal-mismatch",),
        )
        conn.commit()

    with request_context(
        "alice", "req-terminal-mismatch", "run-terminal-mismatch"
    ):
        record_submitted_task(
            {"task_id": "terminal-mismatch", "output_dir": "/safe/terminal"},
            agent="analyst",
        )
        assert current_recorder_degraded() is True
        assert current_accepted_task_ids() == ("terminal-mismatch",)

    stored = registry.get_run("run-terminal-mismatch", owner="alice")
    assert stored is not None
    assert not stored.task_ids


def test_record_handles_research_task_ids_map(tasks_db_path: str) -> None:
    """``research`` returns ``task_ids`` as a name -> id dict.

    Verify every value in the map becomes a child ``tasks`` row under
    one shared ``run_id``; the top-level ``output_dir`` is shared by
    each child since the research wrapper does not nest one per task.
    """
    record_submitted_task(
        {
            "task_ids": {"goal-a": "T-RA", "goal-b": "T-RB"},
            "output_dir": "/obs/research",
        },
        agent="research",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "research"
    assert set(runs[0].task_ids) == {"T-RA", "T-RB"}
    with closed_sqlite_connection(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, output_dir, run_id FROM tasks "
            "WHERE run_id = ? ORDER BY task_id",
            (runs[0].spec.run_id,),
        ).fetchall()
    assert [row[0] for row in rows] == ["T-RA", "T-RB"]
    assert {row[1] for row in rows} == {"/obs/research"}


def test_record_handles_canonical_research_task_ids_list(
    tasks_db_path: str,
) -> None:
    """The canonical Research list remains recordable at the HTTP seam."""
    record_submitted_task(
        {
            "task_ids": ["T-R1", "T-R2"],
            "output_dir": "/obs/research",
        },
        agent="research",
    )

    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "research"
    assert runs[0].task_ids == ("T-R1", "T-R2")


def test_record_prefers_scoped_research_submission_directories(
    tasks_db_path: str,
) -> None:
    """Task rows retain children while the run projects their umbrella."""
    record_submitted_task(
        {
            "research_submissions": [
                {
                    "task_id": "T-R1",
                    "output_dir": "/obs/research/children/part-001",
                },
                {
                    "task_id": "T-R2",
                    "output_dir": "/obs/research/children/part-002",
                },
            ],
            "task_ids": ["legacy-ignored"],
            "output_dir": "/obs/research",
        },
        agent="research",
    )

    run = RunRegistry(tasks_db_path).list_runs(owner="anonymous")[0]
    assert run.result is not None
    assert run.result["execution"]["output_dirs"] == ["/obs/research"]
    with closed_sqlite_connection(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, output_dir FROM tasks WHERE run_id = ? "
            "ORDER BY task_id",
            (run.spec.run_id,),
        ).fetchall()
    assert rows == [
        ("T-R1", "/obs/research/children/part-001"),
        ("T-R2", "/obs/research/children/part-002"),
    ]


def test_record_rejects_mixed_scoped_child_roots(tasks_db_path: str) -> None:
    """Mixed child roots leave no widened umbrella record behind."""
    record_submitted_task(
        {
            "design_task_result": [
                {
                    "task_id": "T-D1",
                    "output_dir": "/obs/one/children/part-001",
                },
                {
                    "task_id": "T-D2",
                    "output_dir": "/obs/two/children/part-002",
                },
            ]
        },
        agent="design",
    )

    assert current_recorder_degraded() is True
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    submit_recorder_module.bind_recorder_degraded(False)


def test_record_persists_safe_submission_warnings(
    tasks_db_path: str,
) -> None:
    """Partial-submit warnings survive the initial registry write."""
    record_submitted_task(
        {
            "task_ids": ["T-W1"],
            "output_dir": "/obs/research",
            "submission_warnings": [
                {
                    "code": "partial_submission",
                    "retryable": False,
                    "rejected_count": 1,
                    "secret": "must not persist",
                }
            ],
        },
        agent="research",
    )

    run = RunRegistry(tasks_db_path).list_runs(owner="anonymous")[0]
    assert run.result is not None
    assert run.result["execution"]["tracking"] == {"degraded": True}
    assert run.result["execution"]["warnings"] == [
        {
            "code": "partial_submission",
            "retryable": False,
            "rejected_count": 1,
        }
    ]


def test_record_handles_network_nested_task(tasks_db_path: str) -> None:
    """``network`` returns one task nested under ``network_task``."""
    record_submitted_task(
        {
            "network_task": {
                "task_id": "T-NET",
                "output_dir": "/obs/network",
            }
        },
        agent="network",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "network"
    assert runs[0].task_ids == ("T-NET",)


def test_record_handles_design_task_result_list(
    tasks_db_path: str,
) -> None:
    """``design`` returns a ``design_task_result`` list of submissions.

    The agent accumulates one AnalystAgent submission dict per design
    kind (protein / promoter / terminator) via LangGraph's
    ``operator.add`` reducer. The chokepoint writes one child row per
    present item under a single shared run.
    """
    record_submitted_task(
        {
            "design_task_result": [
                {
                    "task_id": "T-DP",
                    "output_dir": "/obs/protein",
                },
                {
                    "task_id": "T-DM",
                    "output_dir": "/obs/promoter",
                },
            ],
        },
        agent="design",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "design"
    assert set(runs[0].task_ids) == {"T-DP", "T-DM"}
    with closed_sqlite_connection(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, output_dir FROM tasks "
            "WHERE run_id = ? ORDER BY task_id",
            (runs[0].spec.run_id,),
        ).fetchall()
    output_dirs = dict(rows)
    assert output_dirs == {
        "T-DP": "/obs/protein",
        "T-DM": "/obs/promoter",
    }


def test_record_skips_bind_when_task_row_write_fails(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-success record must not leak a contextvar run_id.

    When ``RunRegistry.create_run`` succeeds but ``TaskManager.record``
    raises mid-loop, the recorder swallows the SQLite error under its
    best-effort contract. The contextvar must stay ``None`` so the
    HTTP layer surfaces ``(None, [])`` instead of a half-populated run
    whose ``task_ids`` column is empty.
    """
    _ = tasks_db_path

    def boom(self: TaskManager, submission: Any) -> None:
        """Raise on every record call to simulate a partial write failure."""
        _ = self, submission
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(TaskManager, "record", boom)
    assert current_run_id() is None
    record_submitted_task(
        {"task_id": "T-fail", "output_dir": "/obs/run"},
        agent="analyst",
    )
    assert current_run_id() is None


def test_record_short_circuits_on_dedup_hit_passthrough(
    tasks_db_path: str,
) -> None:
    """A dedup-hit return must leave the prior task row + run untouched.

    Reproduces the orphan-run scenario the dedup sentinel prevents:
    a first submit records ``T1`` under freshly-minted ``R1``; a
    second handler return for the same ``task_id`` carrying
    ``dedup_hit=True`` would, without the short-circuit, mint a
    sibling ``R2`` and ``INSERT OR REPLACE`` the tasks row so its
    ``run_id`` flips from ``R1`` to ``R2``. The pin: after the second
    call the tasks row's ``run_id`` is still ``R1``, the runs table
    still has exactly one row, and ``current_run_id()`` still points
    at ``R1`` (the chokepoint never minted a new id).
    """
    record_submitted_task(
        {"task_id": "T-dedup", "output_dir": "/obs/run"},
        agent="analyst",
    )
    first_run = current_run_id()
    assert first_run is not None
    runs_before = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs_before) == 1

    record_submitted_task(
        {
            "task_id": "T-dedup",
            "output_dir": "/obs/run",
            "dedup_hit": True,
        },
        agent="analyst",
    )

    with closed_sqlite_connection(tasks_db_path) as conn:
        row = conn.execute(
            "SELECT run_id FROM tasks WHERE task_id = ?",
            ("T-dedup",),
        ).fetchone()
    assert row is not None
    assert row[0] == first_run

    runs_after = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs_after) == 1
    assert runs_after[0].spec.run_id == first_run
    assert runs_after[0].task_ids == ("T-dedup",)
    # The chokepoint never minted a new id on the short-circuit, so the
    # contextvar still points at the prior caller's run.
    assert current_run_id() == first_run


def test_record_logs_and_flags_degraded_on_persistence_failure(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry-write failure logs safe metadata and flags the request.

    The chokepoint catches ``sqlite3.Error`` / ``OSError`` to honour
    the "do not break an already-successful remote submission"
    contract, but the bare ``return`` would leave the failure
    invisible to operators and indistinguishable from the legitimate
    analyst dedup-hit passthrough (both return ``(None, [])`` to the
    HTTP layer). The fix surfaces the failure on two channels:
    ``logger.error`` writes safe identifiers and the exception class,
    and ``bind_recorder_degraded(True)`` flips the request
    contextvar so the HTTP body assembler can attach
    ``degraded_tracking: True`` alongside ``id=None`` /
    ``task_ids=[]``.

    Spies the module logger directly rather than relying on
    ``caplog`` because ``common.logging_config.configure_logging``
    sets ``propagate=False`` on the package logger (so external
    libraries' loggers stay off the root handler), which means
    records emitted by submit_recorder never reach the caplog
    handler attached to root.
    """
    _ = tasks_db_path

    def _raising_create_run(*_args: Any, **_kwargs: Any) -> None:
        """Simulate the persistence failure the contract handles."""
        raise sqlite3.OperationalError("disk I/O error")

    def _exploding_registry_factory(_db_path: str) -> SimpleNamespace:
        """Stand in for ``RunRegistry(db_path)`` so create_run raises."""
        return SimpleNamespace(create_run=_raising_create_run)

    monkeypatch.setattr(
        submit_recorder_module, "RunRegistry", _exploding_registry_factory
    )

    error_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_error(
        msg: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Capture the safe ``logger.error`` payload."""
        del args
        error_calls.append((msg, kwargs.get("extra", {})))

    monkeypatch.setattr(submit_recorder_module.logger, "error", fake_error)
    # Reset the contextvar manually because the test runs outside an
    # HTTP request_context() block — without this, a flag flipped
    # by an earlier test in the same process leaks into this assert.
    submit_recorder_module.bind_recorder_degraded(False)
    assert current_run_id() is None
    assert current_recorder_degraded() is False

    record_submitted_task(
        {"task_id": "T-fail", "output_dir": "/obs/run"},
        agent="analyst",
    )

    assert current_run_id() is None
    assert current_recorder_degraded() is True
    assert len(error_calls) == 1
    rendered_msg, rendered_extra = error_calls[0]
    assert rendered_msg == "Failed to persist remote submission"
    assert rendered_extra == {
        "agent": "analyst",
        "run_id": None,
        "task_count": 1,
        "error_type": "OperationalError",
    }


def test_recorder_keeps_accepted_ids_when_registry_fails(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted upstream ids survive a local registry persistence failure."""
    _ = tasks_db_path
    monkeypatch.setattr(
        submit_recorder_module.RunRegistry,
        "create_run",
        Mock(side_effect=sqlite3.OperationalError("closed")),
    )

    with request_context(user_id="user-1", request_id="request-1"):
        record_submitted_task(
            {"task_id": "accepted-1", "output_dir": "tenant/out"},
            agent="analyst",
        )

        assert current_run_id() is None
        assert current_accepted_task_ids() == ("accepted-1",)
        assert current_recorder_degraded() is True

    assert current_accepted_task_ids() == ()


def test_record_dedup_hit_without_prior_does_not_bind_or_write(
    tasks_db_path: str,
) -> None:
    """A dedup_hit short-circuit must not create state out of thin air.

    Even if the chokepoint is called with ``dedup_hit=True`` before
    any prior submission has been recorded (e.g., a stale registry
    that has been purged), it must not mint a run id, must not write
    any rows, and must not bind the contextvar.
    """
    assert current_run_id() is None
    record_submitted_task(
        {
            "task_id": "T-ghost",
            "output_dir": "/obs/run",
            "dedup_hit": True,
        },
        agent="analyst",
    )
    assert current_run_id() is None
    assert TaskManager(tasks_db_path).get_task("T-ghost") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")
