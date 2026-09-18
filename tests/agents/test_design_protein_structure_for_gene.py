# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Producer-side tests for ``protein_structure_for_gene``.

Pins the new module-level wrapper that deep_genome will reroute its
``protein_structure_analysis`` branch to in commit 2 (AF-019 producer
-first ordering). Asserts the wrapper passes the analyst-subgraph
helper a request dict matching design's ``_dispatch_and_wait_analysis``
shape and propagates the helper's return value verbatim.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.analyst import planning, task_ops
from mcp_server_phytomni.agents.design import agent as design_agent
from mcp_server_phytomni.agents.design.agent import (
    protein_structure_for_gene,
)
from mcp_server_phytomni.agents.shared import analysis
from mcp_server_phytomni.api import run_lifecycle
from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.runtime import task_reconcile
from mcp_server_phytomni.runtime.fingerprint_jobs import (
    FingerprintClaim,
    attach_reuse_claim,
    cancel_run_claims,
    get_latest_job,
    mark_job_terminal,
    register_submitted_job,
)
from mcp_server_phytomni.runtime.request_context import (
    current_request_user,
    request_context,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction
from mcp_server_phytomni.runtime.task_dedup import analyst_task_fingerprint
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager
from tests.support.design_fakes import (
    install_design_dependencies,
    invoke_design_case,
)

pytestmark = pytest.mark.agent


async def test_returns_submit_helper_result_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns the analyst-subgraph helper's projected dict."""
    submit_mock = install_design_dependencies(
        monkeypatch,
        data_uri="obs://data/structure-input",
        task_id="structure-task-id",
        output_dir="obs://run/structure-out",
    )

    result, _request, _request_kwargs = await invoke_design_case(
        protein_structure_for_gene,
        submit_mock,
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert result == {
        "task_id": "structure-task-id",
        "output_dir": "obs://run/structure-out",
        "task_status": "SUCCEEDED",
    }
    submit_mock.assert_awaited_once()


async def test_request_shape_targets_protein_structure_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request dict pins protein_structure_analysis at medium compute.

    The wrapper reads DigitalDesignConfig.COMPUTE_RESOURCE_BY_TYPE
    rather than a submit-site literal.
    """
    submit_mock = install_design_dependencies(
        monkeypatch,
        data_uri="obs://data/structure-input",
        task_id="structure-task-id",
        output_dir="obs://run/structure-out",
    )

    _result, request, request_kwargs = await invoke_design_case(
        protein_structure_for_gene,
        submit_mock,
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert request.analysis_type == "protein_structure_analysis"
    assert request.target_id == "AT1G01010"
    assert request.goal_description == "prompt-stub"
    assert request.meta == "prompt-stub"
    assert request.data_list == {"obs://data/structure-input": "fixture"}
    assert request.compute_resource == "medium"
    assert request_kwargs["is_polling"] is False


async def test_resolves_real_structure_data_list_for_real_species(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AF-001 regression: translate analysis_type to the real JSON key.

    ``get_data_list`` is left UNMOCKED so it hits the bundled
    ``species_data_list.json``. Without the analysis_type ->
    data-list-key translation the lookup raises
    ``KeyError('Analysis type not found: protein_structure_analysis')``;
    the real key is ``structure_analysis``.
    """
    monkeypatch.setattr(
        design_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )
    monkeypatch.setattr(
        design_agent, "AnalystAgent", lambda **_kw: "analyst-agent-stub"
    )
    submit_mock = AsyncMock(
        return_value={
            "task_id": "structure-task-id",
            "output_dir": "obs://run/structure-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(design_agent, "submit_remote_analysis", submit_mock)

    await protein_structure_for_gene(
        species_code="osa",
        gene_id="Os01g0177400",
    )

    call_args = submit_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    data_list = request.data_list
    assert data_list, "expected a non-empty real data list from the JSON"


@pytest.fixture(name="sparse_structure_job")
def _sparse_structure_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """Keep dispatch, probe, and claims real around synthetic I/O."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    data = {"obs://fixture/structure.fa": "synthetic sequence"}
    fingerprint = analyst_task_fingerprint("structure goal", data, None)
    output = "/obs/shared/structure/output/0/"
    manager = TaskManager(db)
    manager.record(
        Submission(
            task_id="EI-root",
            status="submitted",
            output_dir=output,
            input_fingerprint=fingerprint,
        )
    )
    for user in ("alice", "bob"):
        attach_reuse_claim(
            db,
            FingerprintClaim(
                fingerprint,
                "EI-root",
                output,
                f"T-{user}",
                f"run-{user}",
                user,
            ),
        )
    with sqlite_transaction(db) as connection:
        connection.execute(
            "UPDATE tasks SET agent = NULL, created_at = NULL, "
            "task_log = NULL, final_report = NULL"
        )
        connection.execute(
            "UPDATE fingerprint_jobs SET created_at = ?, updated_at = ?",
            ("2026-08-20T13:15:34Z", "2026-08-20T13:15:34Z"),
        )
    monkeypatch.setattr(
        design_agent, "get_prompt", lambda *_a, **_kw: "structure goal"
    )
    monkeypatch.setattr(design_agent, "get_data_list", lambda *_a, **_kw: data)
    monkeypatch.setattr(
        analysis,
        "create_output_dir",
        AsyncMock(return_value="/obs/shared/fresh/output/"),
    )
    submit = AsyncMock(
        return_value={
            "task_id": "EI-fresh",
            "output_dir": "/obs/shared/fresh/output/0/",
            "task_status": "SUBMITTED",
        }
    )
    monkeypatch.setattr(
        design_agent,
        "AnalystAgent",
        lambda **_kw: SimpleNamespace(app=SimpleNamespace(ainvoke=submit)),
    )
    delete = AsyncMock()
    monkeypatch.setattr(ada, "task_delete", delete)
    monkeypatch.setattr(task_ops, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        task_ops, "get_token", AsyncMock(return_value="fixture-token")
    )
    monkeypatch.setattr(
        task_ops, "current_outbound_http_client", lambda _pool: object()
    )
    transport = AsyncMock()
    monkeypatch.setattr(task_ops, "request_response_with_retries", transport)
    return SimpleNamespace(
        db=db,
        manager=manager,
        fingerprint=fingerprint,
        output=output,
        submit=submit,
        delete=delete,
        transport=transport,
    )


def _probe_response(observation: str) -> httpx.Response:
    """Exercise real JSON decoding and live-status parsing."""
    if observation == "invalid-json":
        return httpx.Response(200, text="not json")
    bodies: dict[str, Any] = {
        "missing": {},
        "unknown": {"status": "NEW_STATE"},
        "malformed-body": ["SUCCEEDED"],
        "malformed-status": {"status": ["SUCCEEDED"]},
    }
    return httpx.Response(
        200, json=bodies.get(observation, {"status": observation})
    )


@pytest.mark.parametrize("chained", [False, True], ids=["original", "chain"])
@pytest.mark.parametrize(
    "observation",
    [
        "SUCCEEDED",
        "RUNNING",
        "PENDING",
        "FAILED",
        "CANCELLED",
        "missing",
        "unknown",
        "malformed-body",
        "malformed-status",
        "invalid-json",
        "probe-failure",
    ],
)
async def test_sparse_structure_complete_reuse_path(
    sparse_structure_job: SimpleNamespace,
    observation: str,
    chained: bool,
) -> None:
    """A rejected live probe cannot be undone by job registration."""
    job = sparse_structure_job
    if chained:
        job.manager.record(
            Submission(
                task_id="T-chain",
                status="submitted",
                output_dir=job.output,
                input_fingerprint=job.fingerprint,
                source_task_id="EI-root",
            )
        )
    if observation == "probe-failure":
        job.transport.side_effect = McpError(
            ErrorData(code=INTERNAL_ERROR, message="synthetic probe failure")
        )
    else:
        job.transport.return_value = _probe_response(observation)
    with request_context("carol", "request-carol", "run-carol"):
        result = await protein_structure_for_gene("osa", "fixture-gene")
    assert job.transport.await_count == 1
    assert job.transport.await_args.args[1].url.endswith("/EI-root")
    latest = get_latest_job(job.db, job.fingerprint)
    assert latest is not None
    reused = observation in {"SUCCEEDED", "RUNNING", "PENDING"}
    expected_remote = "EI-root" if reused else "EI-fresh"
    assert latest.ei_task_id == expected_remote
    assert latest.generation == (1 if reused else 2)
    job.delete.assert_not_awaited()
    if reused:
        job.submit.assert_not_awaited()
        assert result["task_id"] not in {"EI-root", "T-chain"}
        assert result["source_task_id"] == "EI-root"
    else:
        job.submit.assert_awaited_once()
        assert job.submit.await_args.args[0]["is_polling"] is False
        assert result["task_id"] == "EI-fresh"
        assert not result.get("source_task_id")
    row = job.manager.get_task(result["task_id"])
    assert row is not None
    assert (row["source_task_id"] or result["task_id"]) == expected_remote
    with sqlite_transaction(job.db) as connection:
        claim = connection.execute(
            "SELECT run_id, user_id, generation, claim_state "
            "FROM fingerprint_job_claims WHERE claimant_task_id = ?",
            (result["task_id"],),
        ).fetchone()
        old_state = connection.execute(
            "SELECT status FROM fingerprint_jobs WHERE generation = 1"
        ).fetchone()[0]
    assert claim == ("run-carol", "carol", latest.generation, "active")
    terminal = {
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    }
    assert old_state == terminal.get(observation, "running")
    cancelled = cancel_run_claims(job.db, run_id="run-carol", user_id="alice")
    assert not cancelled.detached_claimants
    assert not cancelled.terminate_ei_ids
    cancelled = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    assert cancelled.terminate_ei_ids == (() if reused else ("EI-fresh",))
    cancelled = cancel_run_claims(job.db, run_id="run-alice", user_id="alice")
    assert not cancelled.terminate_ei_ids
    cancelled = cancel_run_claims(job.db, run_id="run-bob", user_id="bob")
    assert cancelled.terminate_ei_ids == (
        () if observation in terminal else ("EI-root",)
    )


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED"])
async def test_sparse_structure_status_changes_after_reuse(
    sparse_structure_job: SimpleNamespace,
    terminal: str,
) -> None:
    """A later poll settles the remote source, not the caller-owned ID."""
    job = sparse_structure_job
    job.transport.side_effect = [
        _probe_response("RUNNING"),
        _probe_response(terminal),
    ]
    with request_context("carol", "request-carol", "run-carol"):
        result = await protein_structure_for_gene("osa", "fixture-gene")
    polled = await task_reconcile.reconcile_task(result["task_id"])
    assert polled["status"] == terminal
    assert [
        call.args[1].url.rsplit("/", 1)[-1]
        for call in job.transport.await_args_list
    ] == ["EI-root"] * 2
    latest = get_latest_job(job.db, job.fingerprint)
    assert latest is not None
    assert latest.status == terminal.lower()
    job.submit.assert_not_awaited()
    job.delete.assert_not_awaited()
    for user in ("alice", "bob", "carol"):
        cancelled = cancel_run_claims(
            job.db, run_id=f"run-{user}", user_id=user
        )
        assert not cancelled.terminate_ei_ids


async def test_sparse_structure_rejected_then_verified_fresh_job_is_shared(
    sparse_structure_job: SimpleNamespace,
) -> None:
    """A failed observation opens one job, then verified callers share it."""
    job = sparse_structure_job
    job.transport.side_effect = [
        _probe_response("missing"),
        _probe_response("RUNNING"),
    ]
    with request_context("carol", "request-carol", "run-carol"):
        fresh = await protein_structure_for_gene("osa", "fixture-gene")
    with request_context("dave", "request-dave", "run-dave"):
        shared = await protein_structure_for_gene("osa", "fixture-gene")
    job.submit.assert_awaited_once()
    job.delete.assert_not_awaited()
    assert fresh["task_id"] == "EI-fresh"
    assert shared["task_id"] != fresh["task_id"]
    assert shared["source_task_id"] == fresh["task_id"]
    assert [
        call.args[1].url.rsplit("/", 1)[-1]
        for call in job.transport.await_args_list
    ] == [
        "EI-root",
        "EI-fresh",
    ]
    carol = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    assert not carol.terminate_ei_ids
    dave = cancel_run_claims(job.db, run_id="run-dave", user_id="dave")
    assert dave.terminate_ei_ids == ("EI-fresh",)


@pytest.mark.parametrize("change", ["cancelled", "new-generation"])
async def test_sparse_structure_generation_changes_during_probe(
    sparse_structure_job: SimpleNamespace,
    change: str,
) -> None:
    """A rejected reuse either submits fresh or joins a racing submission."""
    job = sparse_structure_job

    async def remote_reply(*_args: Any, **_kwargs: Any) -> httpx.Response:
        if change == "cancelled":
            mark_job_terminal(job.db, "EI-root", "cancelled")
        else:
            register_submitted_job(
                job.db,
                FingerprintClaim(
                    job.fingerprint,
                    "EI-newer",
                    job.output,
                    "T-dave",
                    "run-dave",
                    "dave",
                ),
                force_new=True,
            )
        return _probe_response("RUNNING")

    job.transport.side_effect = remote_reply
    with request_context("carol", "request-carol", "run-carol"):
        result = await protein_structure_for_gene("osa", "fixture-gene")
    assert job.transport.await_args.args[1].url.endswith("/EI-root")
    assert result["task_id"] == "EI-fresh"
    latest = get_latest_job(job.db, job.fingerprint)
    assert latest is not None
    expected = "EI-fresh" if change == "cancelled" else "EI-newer"
    assert latest.ei_task_id == expected
    assert latest.generation == 2
    job.submit.assert_awaited_once()
    row = job.manager.get_task(result["task_id"])
    assert row is not None
    assert (row["source_task_id"] or result["task_id"]) == expected
    assert _claim_remote(job, result["task_id"]) == expected
    carol = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    if change == "new-generation":
        job.delete.assert_awaited_once_with("EI-fresh")
        assert result["source_task_id"] == "EI-newer"
        assert not carol.terminate_ei_ids
        dave = cancel_run_claims(job.db, run_id="run-dave", user_id="dave")
        assert dave.terminate_ei_ids == ("EI-newer",)
    else:
        job.delete.assert_not_awaited()
        assert carol.terminate_ei_ids == ("EI-fresh",)


def _claim_remote(job: SimpleNamespace, task_id: str) -> str:
    """Resolve the remote job actually owned by one caller's claim."""
    with sqlite_transaction(job.db) as connection:
        return connection.execute(
            "SELECT j.ei_task_id FROM fingerprint_job_claims c "
            "JOIN fingerprint_jobs j ON j.fingerprint = c.fingerprint "
            "AND j.generation = c.generation WHERE c.claimant_task_id = ?",
            (task_id,),
        ).fetchone()[0]


async def test_generation_appears_during_rejected_probe(
    sparse_structure_job: SimpleNamespace,
) -> None:
    """Late materialization cannot override rejection of the same source."""
    job = sparse_structure_job
    with sqlite_transaction(job.db) as connection:
        connection.execute("DELETE FROM fingerprint_job_claims")
        connection.execute("DELETE FROM fingerprint_jobs")

    async def reply(*_args: Any, **_kwargs: Any) -> httpx.Response:
        attach_reuse_claim(
            job.db,
            FingerprintClaim(
                job.fingerprint,
                "EI-root",
                job.output,
                "T-alice",
                "run-alice",
                "alice",
            ),
        )
        return _probe_response("missing")

    job.transport.side_effect = reply
    with request_context("carol", "request-carol", "run-carol"):
        result = await protein_structure_for_gene("osa", "fixture-gene")
    assert _claim_remote(job, result["task_id"]) == result["task_id"]
    job.delete.assert_not_awaited()


@pytest.mark.parametrize(
    "entries", [(False, False), (True, True), (True, False), (False, True)]
)
async def test_two_rejected_probes_share_one_replacement(
    sparse_structure_job: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    entries: tuple[bool, bool],
) -> None:
    """Overlapping retries share a replacement with matching poll identity."""
    job = sparse_structure_job
    carol_probing = asyncio.Event()
    dave_registered = asyncio.Event()

    async def reply(*_args: Any, **_kwargs: Any) -> httpx.Response:
        if current_request_user() == "carol":
            carol_probing.set()
            await asyncio.wait_for(dave_registered.wait(), timeout=3)
        return _probe_response("FAILED")

    async def submit(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {
            "task_id": "EI-" + str(current_request_user()),
            "output_dir": f"/obs/shared/{current_request_user()}/output/0/",
            "task_status": "SUBMITTED",
        }

    async def run(user: str) -> dict[str, Any]:
        with request_context(user, "request-" + user, "run-" + user):
            if entries[0 if user == "carol" else 1]:
                result = await planning.retrieve_plan_submit(
                    "structure goal",
                    {"obs://fixture/structure.fa": "synthetic sequence"},
                )
            else:
                result = await protein_structure_for_gene(
                    "osa", "fixture-gene"
                )
        if user == "dave":
            dave_registered.set()
        return result

    job.transport.side_effect = reply
    job.submit.side_effect = submit
    monkeypatch.setattr(planning, "task_delete", job.delete)
    monkeypatch.setattr(
        planning,
        "_build_submit_agent",
        lambda *_args, **_kwargs: (
            SimpleNamespace(arun=job.submit),
            "/out/fresh/0/",
            "small",
            "thread",
        ),
    )
    carol = asyncio.create_task(run("carol"))
    try:
        await asyncio.wait_for(carol_probing.wait(), timeout=3)
        await run("dave")
        result = await asyncio.wait_for(carol, timeout=3)
    finally:
        if not carol.done():
            carol.cancel()
        await asyncio.gather(carol, return_exceptions=True)
    with sqlite_transaction(job.db) as connection:
        running = connection.execute(
            "SELECT generation, ei_task_id FROM fingerprint_jobs "
            "WHERE status = 'running'"
        ).fetchall()
    assert len(running) == 1
    assert result["task_id"] == "EI-carol"
    assert result["source_task_id"] == "EI-dave"
    job.delete.assert_awaited_once_with("EI-carol")
    row = job.manager.get_task(result["task_id"])
    assert row is not None
    assert (row["source_task_id"] or result["task_id"]) == "EI-dave"
    assert row["output_dir"] == result["output_dir"]
    assert result["output_dir"] == "/obs/shared/dave/output/0/"
    assert _claim_remote(job, result["task_id"]) == "EI-dave"
    assert not cancel_run_claims(
        job.db, run_id="run-dave", user_id="dave"
    ).terminate_ei_ids
    assert cancel_run_claims(
        job.db, run_id="run-carol", user_id="carol"
    ).terminate_ei_ids == ("EI-dave",)
    repeated = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    assert repeated.detached_claimants == repeated.terminate_ei_ids == ()


@pytest.mark.parametrize("observation", ["missing", "invalid-json"])
async def test_direct_entrypoint_rejected_probe_claims_fresh_job(
    sparse_structure_job: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    observation: str,
) -> None:
    """Direct planning registration cannot attach a rejected remote job."""
    job = sparse_structure_job
    job.transport.return_value = _probe_response(observation)
    fresh = {"task_id": "EI-fresh", "output_dir": "/out/fresh/0/"}
    submit = AsyncMock(return_value=fresh)
    monkeypatch.setattr(
        planning,
        "_build_submit_agent",
        lambda *_args, **_kwargs: (
            SimpleNamespace(arun=submit),
            "/out/fresh/0/",
            "small",
            "thread",
        ),
    )
    with request_context("carol", "request-carol", "run-carol"):
        result = await planning.retrieve_plan_submit(
            "structure goal",
            {"obs://fixture/structure.fa": "synthetic sequence"},
        )
    assert _claim_remote(job, result["task_id"]) == result["task_id"]
    cancel = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    assert cancel.terminate_ei_ids == ("EI-fresh",)


async def test_success_probe_does_not_reopen_pending_cancellation(
    sparse_structure_job: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued last-claim termination must not gain a fresh claimant."""
    job = sparse_structure_job
    pending_termination: list[str] = []

    async def reply(*_args: Any, **_kwargs: Any) -> httpx.Response:
        for user in ("alice", "bob"):
            cancellation = cancel_run_claims(
                job.db, run_id="run-" + user, user_id=user
            )
            pending_termination.extend(cancellation.terminate_ei_ids)
        latest = get_latest_job(job.db, job.fingerprint)
        assert latest is not None and latest.status == "cancelling"
        return _probe_response("SUCCEEDED")

    deleted = AsyncMock()
    monkeypatch.setattr(run_lifecycle, "task_delete", deleted)
    job.transport.side_effect = reply
    with request_context("carol", "request-carol", "run-carol"):
        result = await protein_structure_for_gene("osa", "fixture-gene")
    terminate_jobs = getattr(run_lifecycle, "_terminate_last_claim_jobs")
    await terminate_jobs(job.db, pending_termination)
    assert _claim_remote(job, result["task_id"]) not in pending_termination
    deleted.assert_awaited_once_with("EI-root")


async def test_probe_cancellation_propagates_without_new_claim(
    sparse_structure_job: SimpleNamespace,
) -> None:
    """Probe cancellation propagates without compute or claim side effects."""
    job = sparse_structure_job
    job.transport.side_effect = asyncio.CancelledError()
    with (
        request_context("carol", "request-carol", "run-carol"),
        pytest.raises(asyncio.CancelledError),
    ):
        await protein_structure_for_gene("osa", "fixture-gene")
    job.submit.assert_not_awaited()
    job.delete.assert_not_awaited()
    cancel = cancel_run_claims(job.db, run_id="run-carol", user_id="carol")
    assert not cancel.detached_claimants
