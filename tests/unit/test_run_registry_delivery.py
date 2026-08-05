# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Durable result archive-delivery lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.mcp.formatting.models import (
    ExecutionProjection,
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.mcp.formatting.redaction import strip_agent_result
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    inventory_digest,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_delivery import (
    ResultDeliveryDependencies,
    claim_delivery_attempt,
    settle_delivery_failure,
    settle_delivery_ready,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)


def _inventory() -> ResultArchiveInventory:
    member = ResultArchiveMember(
        1,
        "/obs/phytomni/runs/run-1/children/part-001/report.md",
        "results/part-001/report.md",
        ArtifactRole.SCIENTIFIC_REPORT,
        "text/markdown",
        3,
    )
    return ResultArchiveInventory(
        "/obs/phytomni/runs/run-1",
        (member,),
        inventory_digest((member,)),
        3,
    )


def _pending_result(inventory: ResultArchiveInventory) -> dict:
    return {
        "formatted": {"answer": "scientific answer"},
        "execution": {
            "tracking": {"degraded": False},
            "warnings": [],
            "delivery": {
                "schema_version": 1,
                "required": True,
                "status": "pending",
                "revision": 1,
                "inventory_digest": inventory.digest,
                "archive": None,
                "error_code": None,
                "retryable": False,
            },
        },
        "delivery_internal": {
            "inventory_ref": (
                f"{inventory.run_root}/delivery/"
                f"{inventory.digest.removeprefix('sha256:')}/"
                ".phytomni-result-inventory.json"
            ),
            "attempts_claimed": 0,
            "last_error_code": None,
        },
    }


def _registry(
    tmp_path: Path, dependencies: ResultDeliveryDependencies
) -> tuple[RunRegistry, ResultArchiveInventory]:
    inventory = _inventory()
    registry = RunRegistry(
        str(tmp_path / "tasks.db"), delivery_dependencies=dependencies
    )
    registry.create_run(
        RunSpec("run-1", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="running", result=_pending_result(inventory)),
    )
    TaskManager(registry.db_path).record(
        Submission(
            task_id="child-1",
            status="succeeded",
            output_dir="/obs/phytomni/runs/run-1/children/part-001",
            run_context=RunContext(
                run_id="run-1",
                user_id="alice",
                agent="analyst",
                origin="remote",
                created_at="2026-08-05T00:00:00+00:00",
                updated_at="2026-08-05T00:00:00+00:00",
            ),
        )
    )
    return registry, inventory


def test_delivery_models_project_under_execution() -> None:
    """A pending archive requirement has no public private state."""
    delivery = ResultDelivery(
        schema_version=1,
        required=True,
        status="pending",
        revision=1,
        inventory_digest="",
        archive=None,
        error_code=None,
        retryable=False,
    )

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


def test_automatic_attempts_are_capped_then_manual_retry_reuses_inventory(
    tmp_path: Path,
) -> None:
    """Three retryable failures settle science, then manual retry succeeds."""
    calls = 0

    def publish(
        inventory: ResultArchiveInventory, *, agent: str, summary_markdown: str
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
            "run-1",
            owner="alice",
            revision=1,
            inventory_digest=inventory.digest,
        )
        assert claim is not None
        assert claim.attempts_claimed == attempt
        with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
            publish(inventory, agent="analyst", summary_markdown="scientific answer")
        outcome = settle_delivery_failure(
            registry,
            "run-1",
            owner="alice",
            revision=1,
            inventory_digest=inventory.digest,
            error_code="archive_publish_failed",
            retryable=True,
        )
        assert outcome == ("failed" if attempt == 3 else "retry")

    failed = registry.get_run("run-1", owner="alice")
    assert failed is not None
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
        "run-1",
        owner="alice",
        revision=2,
        inventory_digest=inventory.digest,
    )
    assert claim is not None
    settle_delivery_ready(
        registry,
        "run-1",
        owner="alice",
        revision=2,
        inventory_digest=inventory.digest,
        archive=publish(inventory, agent="analyst", summary_markdown="scientific answer"),
    )

    ready = registry.get_run("run-1", owner="alice")
    assert ready is not None
    assert ready.status == "succeeded"
    assert ready.task_ids == ("child-1",)
    assert ready.result["execution"]["delivery"]["status"] == "ready"
    assert ready.result["execution"]["delivery"]["revision"] == 2
    assert ready.result["execution"]["delivery"]["inventory_digest"] == inventory.digest
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
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
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
    registry.settle_run("run-1", owner="alice", status="succeeded", result=result)

    assert registry.begin_delivery_retry("run-1", owner="foreign") is False
    assert registry.begin_delivery_retry("run-1", owner="alice") is False


def test_private_delivery_state_is_stripped_even_for_debug_projection() -> None:
    """The private inventory reference never survives generic result redaction."""
    result = _pending_result(_inventory())

    public = strip_agent_result(result)

    assert "delivery_internal" not in public
    assert "inventory_ref" not in str(public)


@pytest.mark.asyncio
async def test_reconcile_relaunches_pending_delivery_without_polling_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart recovery schedules only delivery when private inventory exists."""
    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
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
        raise AssertionError("pending delivery must not poll scientific children")

    monkeypatch.setattr(run_registry_module, "reconcile_task", unexpected_poll)
    pending = await registry.reconcile("run-1", owner="alice")

    assert pending is not None
    assert pending.status == "running"
    assert pending.result["execution"]["delivery"]["inventory_digest"] == inventory.digest
    assert scheduled == [("run-1", 1)]


@pytest.mark.asyncio
async def test_reconcile_exhausted_pending_delivery_never_claims_a_fourth_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart with three durable claims settles failed without publishing."""
    async def no_sleep(_seconds: float) -> None:
        return None

    registry, inventory = _registry(
        tmp_path,
        ResultDeliveryDependencies(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
            sleep=no_sleep,
        ),
    )
    result = _pending_result(inventory)
    result["delivery_internal"]["attempts_claimed"] = 3
    result["delivery_internal"]["last_error_code"] = "archive_publish_failed"
    assert registry.update_running_result("run-1", owner="alice", result=result)
    monkeypatch.setattr(
        run_registry_module,
        "claim_delivery_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    record = await registry.reconcile("run-1", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert record.result["execution"]["delivery"]["status"] == "failed"
    assert record.result["delivery_internal"]["attempts_claimed"] == 3
