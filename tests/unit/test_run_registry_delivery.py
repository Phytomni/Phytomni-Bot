# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable result archive-delivery lifecycle contracts."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mcp_server_phytomni.mcp.formatting.models import (
    ExecutionProjection,
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.mcp.formatting.redaction import strip_agent_result
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime import (
    run_registry_delivery as delivery_module,
)
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    inventory_digest,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_delivery import (
    DeliveryFailure,
    DeliveryRevision,
    ResultDeliveryDependencies,
    claim_delivery_attempt,
    run_delivery_worker,
    settle_delivery_failure,
    settle_delivery_ready,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)


def _inventory() -> ResultArchiveInventory:
    source_path = "/obs/phytomni/runs/run-1/children/part-001/report.md"
    archive_path = "results/part-001/report.md"
    member = ResultArchiveMember(
        child_index=1,
        download_ref=source_path,
        archive_path=archive_path,
        role=ArtifactRole.SCIENTIFIC_REPORT,
        media_type="text/markdown",
        size_bytes=3,
    )
    members = (member,)
    return ResultArchiveInventory(
        run_root="/obs/phytomni/runs/run-1",
        members=members,
        digest=inventory_digest(members),
        total_size_bytes=3,
    )


def _pending_result(inventory: ResultArchiveInventory) -> dict:
    result = empty_execution_projection(result_archive_required=True)
    result["formatted"]["answer"] = "scientific answer"
    result["execution"]["delivery"]["inventory_digest"] = inventory.digest
    result["delivery_internal"] = {
        "inventory_ref": (
            f"{inventory.run_root}/delivery/"
            f"{inventory.digest.removeprefix('sha256:')}/"
            ".phytomni-result-inventory.json"
        ),
        "attempts_claimed": 0,
        "last_error_code": None,
    }
    return result


def _run_context() -> RunContext:
    """Return the canonical child-task ownership context for this suite."""
    timestamp = "2026-08-05T00:00:00+00:00"
    return RunContext(
        "run-1",
        "alice",
        "analyst",
        "remote",
        timestamp,
        timestamp,
    )


def _delivery_target(
    inventory: ResultArchiveInventory, revision: int = 1
) -> DeliveryRevision:
    """Return the owned immutable identity used by delivery operations."""
    return DeliveryRevision("run-1", "alice", revision, inventory.digest)


def _registry(
    tmp_path: Path, dependencies: ResultDeliveryDependencies
) -> tuple[RunRegistry, ResultArchiveInventory]:
    inventory = _inventory()
    registry = RunRegistry(
        str(tmp_path / "tasks.db"), delivery_dependencies=dependencies
    )
    registry.create_run(
        RunSpec("run-1", "alice", "analyst", "remote"),
        outcome=RunOutcome(
            status="running", result=_pending_result(inventory)
        ),
    )
    TaskManager(registry.db_path).record(
        Submission(
            task_id="child-1",
            status="succeeded",
            output_dir="/obs/phytomni/runs/run-1/children/part-001",
            run_context=_run_context(),
        )
    )
    return registry, inventory


def test_delivery_models_project_under_execution() -> None:
    """A pending archive requirement has no public private state."""
    delivery = ResultDelivery(1, True, "pending", 1, "", None, None, False)

    assert ExecutionProjection(delivery=delivery).delivery == delivery


def test_ready_delivery_exposes_only_opaque_archive_reference() -> None:
    """Ready archive metadata remains public without an OBS object path."""
    archive = ResultArchiveDescriptor(
        role="result_archive",
        name="analyst-results.zip",
        media_type="application/zip",
        size_bytes=12,
        downloadable=True,
        report_context_eligible=False,
        download_ref="result-archive:sha256:" + "a" * 64,
    )
    delivery = ResultDelivery(
        schema_version=1,
        required=True,
        status="ready",
        revision=2,
        inventory_digest="sha256:" + "a" * 64,
        archive=archive,
        error_code=None,
        retryable=False,
    )

    assert delivery.archive == archive


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "scientific_data"},
        {"name": "foo-results.zip"},
        {"name": "../analyst-results.zip"},
        {"media_type": "application/x-zip-compressed"},
        {"size_bytes": True},
        {"size_bytes": -1},
        {"downloadable": 1},
        {"report_context_eligible": 0},
        {"download_ref": "/obs/private/archive.zip"},
        {"download_ref": "result-archive:sha256:" + "G" * 64},
    ],
)
def test_archive_descriptor_rejects_invalid_runtime_values(
    changes: dict,
) -> None:
    """Runtime validation rejects malformed public descriptors uniformly."""
    baseline = {
        "role": "result_archive",
        "name": "analyst-results.zip",
        "media_type": "application/zip",
        "size_bytes": 12,
        "downloadable": True,
        "report_context_eligible": False,
        "download_ref": "result-archive:sha256:" + "a" * 64,
    }

    with pytest.raises(ValueError, match="invalid result archive descriptor"):
        ResultArchiveDescriptor(**(baseline | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"required": False},
        {"status": "unknown"},
        {"revision": True},
        {"revision": 0},
        {"inventory_digest": "sha256:" + "g" * 64},
        {"retryable": 1},
        {"archive": "not-a-descriptor"},
        {
            "status": "pending",
            "archive": ResultArchiveDescriptor(
                "result_archive",
                "analyst-results.zip",
                "application/zip",
                1,
                True,
                False,
                "result-archive:sha256:" + "a" * 64,
            ),
        },
        {"status": "failed", "error_code": None},
    ],
)
def test_delivery_rejects_invalid_runtime_values(changes: dict) -> None:
    """Delivery invariants reject types, states, and inconsistent payloads."""
    baseline = empty_execution_projection(result_archive_required=True)[
        "execution"
    ]["delivery"]

    with pytest.raises(ValueError, match="invalid result delivery state"):
        ResultDelivery(**(baseline | changes))


def test_ready_delivery_requires_matching_opaque_archive_reference() -> None:
    """A ready archive must be bound to the exact immutable digest."""
    archive = ResultArchiveDescriptor(
        "result_archive",
        "analyst-results.zip",
        "application/zip",
        1,
        True,
        False,
        "result-archive:sha256:" + "b" * 64,
    )

    with pytest.raises(ValueError, match="invalid result delivery state"):
        ResultDelivery(
            1, True, "ready", 1, "sha256:" + "a" * 64, archive, None, False
        )


def test_automatic_attempts_are_capped_then_manual_retry_reuses_inventory(
    tmp_path: Path,
) -> None:
    """Three retryable failures settle science, then manual retry succeeds."""
    calls = 0

    def publish(
        inventory: ResultArchiveInventory, agent: str, summary_markdown: str
    ) -> ResultArchiveDescriptor:
        nonlocal calls
        calls += 1
        assert agent == "analyst"
        assert summary_markdown == "scientific answer"
        if calls <= 3:
            raise ResultArchiveError("archive_publish_failed", retryable=True)
        return ResultArchiveDescriptor(
            role="result_archive",
            name="analyst-results.zip",
            media_type="application/zip",
            size_bytes=12,
            downloadable=True,
            report_context_eligible=False,
            download_ref=f"result-archive:{inventory.digest}",
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path, ResultDeliveryDependencies(publish=publish, sleep=no_sleep)
    )
    for attempt in range(1, 4):
        claim = claim_delivery_attempt(
            registry,
            _delivery_target(inventory),
        )
        assert claim is not None
        assert claim.attempts_claimed == attempt
        with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
            publish(
                inventory,
                agent="analyst",
                summary_markdown="scientific answer",
            )
        outcome = settle_delivery_failure(
            registry,
            _delivery_target(inventory),
            DeliveryFailure("archive_publish_failed", True),
        )
        assert outcome == ("failed" if attempt == 3 else "retry")

    failed = registry.get_run("run-1", owner="alice")
    assert failed is not None
    assert failed.result is not None
    assert failed.status == "succeeded"
    assert failed.task_ids == ("child-1",)
    assert failed.result["execution"]["delivery"] == {
        "schema_version": 1,
        "required": True,
        "status": "failed",
        "revision": 1,
        "inventory_digest": inventory.digest,
        "archive": None,
        "error_code": "archive_publish_failed",
        "retryable": True,
    }
    assert failed.result["delivery_internal"]["attempts_claimed"] == 3
    assert registry.begin_delivery_retry("run-1", owner="alice") is True
    assert registry.begin_delivery_retry("run-1", owner="alice") is False

    claim = claim_delivery_attempt(
        registry,
        _delivery_target(inventory, revision=2),
    )
    assert claim is not None
    settle_delivery_ready(
        registry,
        _delivery_target(inventory, revision=2),
        publish(
            inventory, agent="analyst", summary_markdown="scientific answer"
        ),
    )

    ready = registry.get_run("run-1", owner="alice")
    assert ready is not None
    assert ready.result is not None
    assert ready.status == "succeeded"
    assert ready.task_ids == ("child-1",)
    assert ready.result["execution"]["delivery"]["status"] == "ready"
    assert ready.result["execution"]["delivery"]["revision"] == 2
    assert (
        ready.result["execution"]["delivery"]["inventory_digest"]
        == inventory.digest
    )
    assert calls == 4


def test_manual_retry_owner_and_ready_checks_fail_closed(
    tmp_path: Path,
) -> None:
    """Foreign and ready rows are indistinguishable rejected retry requests."""

    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError()
            ),
            sleep=no_sleep,
        ),
    )
    result = _pending_result(inventory)
    result["execution"]["delivery"]["status"] = "ready"
    result["execution"]["delivery"]["archive"] = {
        "role": "result_archive",
        "name": "analyst-results.zip",
        "media_type": "application/zip",
        "size_bytes": 1,
        "downloadable": True,
        "report_context_eligible": False,
        "download_ref": f"result-archive:{inventory.digest}",
    }
    current = registry.get_run("run-1", owner="alice")
    assert current is not None
    registry.settle_run(
        "run-1",
        owner="alice",
        status="succeeded",
        result=result,
        expected_revision=current.revision,
    )

    assert registry.begin_delivery_retry("run-1", owner="foreign") is False
    assert registry.begin_delivery_retry("run-1", owner="alice") is False


def test_private_delivery_state_is_stripped_even_for_debug_projection() -> (
    None
):
    """Private inventory references never survive result redaction."""
    result = _pending_result(_inventory())

    public = strip_agent_result(result)

    assert "delivery_internal" not in public
    assert "inventory_ref" not in str(public)


def test_stale_revision_cannot_settle_current_delivery(tmp_path: Path) -> None:
    """An older delivery worker cannot replace the new state."""

    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError()
            ),
            sleep=no_sleep,
        ),
    )
    for attempt in range(1, 4):
        claim = claim_delivery_attempt(
            registry,
            _delivery_target(inventory),
        )
        assert claim is not None and claim.attempts_claimed == attempt
        outcome = settle_delivery_failure(
            registry,
            _delivery_target(inventory),
            DeliveryFailure("archive_publish_failed", True),
        )
        assert outcome == ("failed" if attempt == 3 else "retry")
    assert registry.begin_delivery_retry("run-1", owner="alice") is True
    current = registry.get_run("run-1", owner="alice")
    assert current is not None
    assert current.result is not None
    assert current.result["execution"]["delivery"]["revision"] == 2
    archive = ResultArchiveDescriptor(
        "result_archive",
        "analyst-results.zip",
        "application/zip",
        1,
        True,
        False,
        f"result-archive:{inventory.digest}",
    )

    assert not settle_delivery_ready(
        registry,
        _delivery_target(inventory),
        archive,
    )
    winner = registry.get_run("run-1", owner="alice")
    assert winner is not None
    assert winner.status == "running"
    assert winner.result is not None
    assert winner.result["execution"]["delivery"]["revision"] == 2


@pytest.mark.asyncio
async def test_worker_executes_in_to_thread_with_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker runs a synchronous publisher off the event-loop thread."""
    publisher_threads: list[int] = []

    def publish(
        inventory: ResultArchiveInventory, agent: str, summary_markdown: str
    ) -> ResultArchiveDescriptor:
        publisher_threads.append(threading.get_ident())
        del agent, summary_markdown
        return ResultArchiveDescriptor(
            "result_archive",
            "analyst-results.zip",
            "application/zip",
            1,
            True,
            False,
            f"result-archive:{inventory.digest}",
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    dependencies = ResultDeliveryDependencies(publish=publish, sleep=no_sleep)
    registry, inventory = _registry(tmp_path, dependencies)
    monkeypatch.setattr(
        delivery_module, "load_private_inventory", lambda *_args: inventory
    )

    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    previous_executor = getattr(loop, "_default_executor", None)
    loop.set_default_executor(executor)
    try:
        await asyncio.wait_for(
            run_delivery_worker(
                registry,
                _delivery_target(inventory),
                dependencies,
                "worker-thread-test",
            ),
            timeout=2,
        )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        setattr(loop, "_default_executor", previous_executor)

    assert publisher_threads and publisher_threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_reconcile_relaunches_pending_delivery_without_polling_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart recovery schedules only a pending delivery."""

    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError()
            ),
            sleep=no_sleep,
        ),
    )
    scheduled: list[tuple[str, int]] = []
    monkeypatch.setattr(
        registry,
        "_schedule_delivery",
        lambda current, delivery: scheduled.append(
            (current.spec.run_id, delivery.revision)
        ),
    )

    async def unexpected_poll(_task_id: str) -> dict:
        raise AssertionError(
            "pending delivery must not poll scientific children"
        )

    monkeypatch.setattr(run_registry_module, "reconcile_task", unexpected_poll)
    pending = await registry.reconcile("run-1", owner="alice")

    assert pending is not None
    assert pending.result is not None
    assert pending.status == "running"
    assert (
        pending.result["execution"]["delivery"]["inventory_digest"]
        == inventory.digest
    )
    assert scheduled == [("run-1", 1)]


@pytest.mark.asyncio
async def test_exhausted_delivery_never_claims_a_fourth_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three durable claims settle as failed without publication."""

    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError()
            ),
            sleep=no_sleep,
        ),
    )
    result = _pending_result(inventory)
    result["delivery_internal"]["attempts_claimed"] = 3
    result["delivery_internal"]["last_error_code"] = "archive_publish_failed"
    assert registry.update_running_result(
        "run-1", owner="alice", result=result
    )
    monkeypatch.setattr(
        delivery_module,
        "claim_delivery_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    record = await registry.reconcile("run-1", owner="alice")

    assert record is not None
    assert record.result is not None
    assert record.status == "succeeded"
    assert record.result["execution"]["delivery"]["status"] == "failed"
    assert record.result["delivery_internal"]["attempts_claimed"] == 3


def test_update_running_result_keeps_submit_delivery_marker(
    tmp_path: Path,
) -> None:
    """A later running projection must not drop submit-time archive delivery.

    HTTP background workers overwrite the reserved result with a formatted
    tool envelope that has ``delivery: null``. Harvest only packs a zip
    when ``delivery.required`` is still on the stored run.
    """
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-keep-delivery",
            user_id="alice",
            agent="analyst",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-keep-delivery"),
        result=empty_execution_projection(result_archive_required=True),
    )
    incoming = empty_execution_projection()
    incoming["execution"]["tasks"] = [
        {"id": "task-1", "accepted": True, "status": "submitted"}
    ]
    incoming["execution"]["delivery"] = None

    assert registry.update_running_result(
        "run-keep-delivery",
        owner="alice",
        result=incoming,
    )
    record = registry.get_run("run-keep-delivery", owner="alice")
    assert record is not None
    assert record.result is not None
    delivery = record.result["execution"]["delivery"]
    assert delivery["required"] is True
    assert delivery["status"] == "pending"
    assert record.result["execution"]["tasks"][0]["id"] == "task-1"


def test_update_running_result_keeps_recorder_task_kinds(
    tmp_path: Path,
) -> None:
    """A formatted envelope must not drop submit-time kind/error_code rows."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    reserved = empty_execution_projection(result_archive_required=True)
    reserved["execution"]["tasks"] = [
        {
            "id": "child-accepted",
            "accepted": True,
            "status": "submitted",
            "kind": "protein_structure_analysis",
            "error_code": None,
        },
        {
            "id": "child-failed",
            "accepted": False,
            "status": "failed",
            "kind": "promoter_analysis",
            "error_code": "input_rejected",
        },
    ]
    registry.reserve_run(
        RunSpec(
            run_id="run-keep-kinds",
            user_id="alice",
            agent="design",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-keep-kinds"),
        result=reserved,
    )
    incoming = empty_execution_projection()
    incoming["execution"]["tasks"] = [
        {"id": "child-accepted", "accepted": True}
    ]
    incoming["execution"]["delivery"] = None

    assert registry.update_running_result(
        "run-keep-kinds",
        owner="alice",
        result=incoming,
    )
    record = registry.get_run("run-keep-kinds", owner="alice")
    assert record is not None
    assert record.result is not None
    assert (
        record.result["execution"]["tasks"] == reserved["execution"]["tasks"]
    )
