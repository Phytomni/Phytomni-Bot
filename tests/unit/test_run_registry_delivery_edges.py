# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for archive-delivery validation and helpers."""

# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.mcp.formatting.models import ResultArchiveDescriptor
from mcp_server_phytomni.runtime import (
    run_registry_delivery as delivery_module,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_delivery import (
    DeliveryFailure,
    DeliveryRevision,
    PrivateDeliveryState,
    begin_delivery_retry,
    carry_required_delivery,
    claim_delivery_attempt,
    delivery_backoff,
    initial_pending_delivery,
    load_private_inventory,
    mark_degraded_delivery_failure,
    private_delivery_from_result,
    result_delivery_from_result,
    run_delivery_worker,
    settle_delivery_failure,
    settle_delivery_ready,
)

pytestmark = pytest.mark.unit

_DIGEST = "sha256:" + "a" * 64


def _archive() -> ResultArchiveDescriptor:
    """Return one valid public archive descriptor."""
    return ResultArchiveDescriptor(
        role="result_archive",
        name="analyst-results.zip",
        media_type="application/zip",
        size_bytes=12,
        downloadable=True,
        report_context_eligible=False,
        download_ref=f"result-archive:{_DIGEST}",
    )


def test_private_delivery_state_rejects_invalid_values() -> None:
    """Empty refs and out-of-range attempt counts fail closed."""
    with pytest.raises(ValueError, match="invalid private delivery state"):
        PrivateDeliveryState("", 0, None)
    with pytest.raises(ValueError, match="invalid private delivery state"):
        PrivateDeliveryState("ref", -1, None)
    with pytest.raises(ValueError, match="invalid private delivery state"):
        PrivateDeliveryState("ref", 4, None)


def test_delivery_revision_rejects_invalid_identity() -> None:
    """Revision identity requires owner, digest, and a positive int."""
    with pytest.raises(ValueError, match="invalid delivery revision"):
        DeliveryRevision("", "alice", 1, _DIGEST)
    with pytest.raises(ValueError, match="invalid delivery revision"):
        DeliveryRevision("run-1", "alice", True, _DIGEST)
    with pytest.raises(ValueError, match="invalid delivery revision"):
        DeliveryRevision("run-1", "alice", 0, _DIGEST)


def test_delivery_failure_rejects_invalid_values() -> None:
    """Failure records require a non-empty code and a real bool."""
    with pytest.raises(ValueError, match="invalid delivery failure"):
        DeliveryFailure("", True)
    with pytest.raises(ValueError, match="invalid delivery failure"):
        DeliveryFailure(
            "archive_publish_failed",
            "yes",  # type: ignore[arg-type]
        )


def test_delivery_backoff_and_initial_pending() -> None:
    """Backoff is bounded and the first revision starts pending."""
    assert delivery_backoff(0) == 0.05
    assert delivery_backoff(1) == 0.05
    assert delivery_backoff(2) == 0.1
    pending = initial_pending_delivery(_DIGEST)
    assert pending.status == "pending"
    assert pending.revision == 1
    assert pending.inventory_digest == _DIGEST


def test_result_mapping_handles_invalid_json() -> None:
    """Corrupt or non-mapping result payloads become an empty dict."""
    assert delivery_module._result_mapping("{") == {}
    assert delivery_module._result_mapping(12) == {}


def test_retryable_failed_delivery_guards() -> None:
    """Manual retry requires a failed retryable delivery without archive."""
    pending = initial_pending_delivery(_DIGEST)
    private = PrivateDeliveryState("ref", 3, "archive_publish_failed")
    assert delivery_module._retryable_failed_delivery(None, private) is False
    assert (
        delivery_module._retryable_failed_delivery(pending, private) is False
    )
    failed = pending.__class__(
        schema_version=1,
        required=True,
        status="failed",
        revision=1,
        inventory_digest=_DIGEST,
        archive=None,
        error_code="archive_publish_failed",
        retryable=True,
    )
    empty_digest = failed.__class__(
        schema_version=1,
        required=True,
        status="failed",
        revision=1,
        inventory_digest="",
        archive=None,
        error_code="archive_publish_failed",
        retryable=False,
    )
    with_archive = object.__new__(failed.__class__)
    object.__setattr__(with_archive, "status", "failed")
    object.__setattr__(with_archive, "retryable", True)
    object.__setattr__(with_archive, "archive", _archive())
    object.__setattr__(with_archive, "inventory_digest", _DIGEST)
    assert delivery_module._retryable_failed_delivery(failed, private) is True
    assert (
        delivery_module._retryable_failed_delivery(empty_digest, private)
        is False
    )
    assert (
        delivery_module._retryable_failed_delivery(with_archive, private)
        is False
    )


def test_delivery_match_and_claimable_guards() -> None:
    """Claim/settle helpers reject missing, non-pending, or exhausted rows."""
    target = DeliveryRevision("run-1", "alice", 1, _DIGEST)
    pending = initial_pending_delivery(_DIGEST)
    private = PrivateDeliveryState("ref", 0, None)
    exhausted = PrivateDeliveryState("ref", 3, None)
    assert (
        delivery_module._delivery_matches_target(None, private, target)
        is False
    )
    ready = pending.__class__(
        schema_version=1,
        required=True,
        status="ready",
        revision=1,
        inventory_digest=_DIGEST,
        archive=_archive(),
        error_code=None,
        retryable=False,
    )
    assert (
        delivery_module._delivery_matches_target(ready, private, target)
        is False
    )
    other = DeliveryRevision("run-1", "alice", 2, _DIGEST)
    assert (
        delivery_module._delivery_matches_target(pending, private, other)
        is False
    )
    assert delivery_module._claimable_delivery(None, None, target) is False
    assert (
        delivery_module._claimable_delivery(pending, exhausted, target)
        is False
    )
    assert (
        delivery_module._claimable_delivery(pending, private, target) is True
    )


def test_result_and_private_delivery_parsers() -> None:
    """Corrupt public and private delivery blocks parse as absent."""
    assert result_delivery_from_result("nope") is None
    assert (
        result_delivery_from_result(
            {"execution": {"delivery": {"status": "pending"}}}
        )
        is None
    )
    assert private_delivery_from_result("nope") is None
    assert private_delivery_from_result({"delivery_internal": 1}) is None
    assert (
        private_delivery_from_result(
            {
                "delivery_internal": {
                    "inventory_ref": "",
                    "attempts_claimed": 0,
                    "last_error_code": None,
                }
            }
        )
        is None
    )


def test_carry_required_delivery_copies_private_block() -> None:
    """A required stored delivery is copied with its private coordinator."""
    stored = {
        "execution": {
            "delivery": {
                "schema_version": 1,
                "required": True,
                "status": "pending",
                "revision": 1,
                "inventory_digest": _DIGEST,
                "archive": None,
                "error_code": None,
                "retryable": False,
            }
        },
        "delivery_internal": {
            "inventory_ref": "obs://root/delivery/"
            + _DIGEST.removeprefix("sha256:")
            + "/.phytomni-result-inventory.json",
            "attempts_claimed": 0,
            "last_error_code": None,
        },
    }
    merged = carry_required_delivery(stored, {"execution": {}})
    assert merged["execution"]["delivery"]["required"] is True
    assert merged["delivery_internal"]["attempts_claimed"] == 0


def test_mark_degraded_delivery_ignores_non_dict_execution() -> None:
    """Degraded marking is a no-op without a mutable execution mapping."""
    result: dict[str, object] = {"execution": "missing"}
    mark_degraded_delivery_failure(result, True)
    assert result["execution"] == "missing"


def test_claim_and_settle_ignore_missing_or_stale_rows(tmp_path: Path) -> None:
    """Claim/settle helpers return None/stale when the running row is gone."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    target = DeliveryRevision("run-1", "alice", 1, _DIGEST)
    assert claim_delivery_attempt(registry, target) is None
    assert (
        settle_delivery_failure(
            registry, target, DeliveryFailure("archive_publish_failed", True)
        )
        == "stale"
    )
    assert settle_delivery_ready(registry, target, _archive()) is False


def test_claim_rejects_non_claimable_running_row(tmp_path: Path) -> None:
    """A running row without pending delivery cannot be claimed."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-1", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="running", result={"execution": {}}),
    )
    target = DeliveryRevision("run-1", "alice", 1, _DIGEST)
    assert claim_delivery_attempt(registry, target) is None
    assert (
        settle_delivery_failure(
            registry, target, DeliveryFailure("archive_publish_failed", True)
        )
        == "stale"
    )


async def test_worker_returns_when_claim_is_unavailable(
    tmp_path: Path,
) -> None:
    """The worker exits immediately when no claim can be taken."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    target = DeliveryRevision("run-1", "alice", 1, _DIGEST)
    await run_delivery_worker(
        registry,
        target,
        delivery_module.ResultDeliveryDependencies(
            publish=lambda *args: _archive(),
            sleep=AsyncMock(),
        ),
        "delivery:run-1:1",
    )


async def test_load_private_inventory_rejects_malformed_ref() -> None:
    """Inventory refs must end with the digest-scoped manifest name."""
    with pytest.raises(ResultArchiveError):
        await load_private_inventory("not-a-ref", _DIGEST)
    with pytest.raises(ResultArchiveError):
        await load_private_inventory(
            "obs://root/delivery/wrong/.phytomni-result-inventory.json",
            _DIGEST,
        )


async def test_load_private_inventory_delegates_to_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A well-formed ref reloads the immutable inventory snapshot."""
    inventory = ResultArchiveInventory(
        run_root="/obs/root",
        members=(),
        digest=_DIGEST,
        total_size_bytes=0,
    )

    async def _load(*args: Any, **kwargs: Any) -> ResultArchiveInventory:
        del args, kwargs
        return inventory

    monkeypatch.setattr(
        delivery_module,
        "load_result_archive_inventory_with_runtime",
        _load,
    )
    monkeypatch.setattr(
        delivery_module, "current_obs_runtime", lambda: object()
    )
    tail = (
        f"{_DIGEST.removeprefix('sha256:')}/" ".phytomni-result-inventory.json"
    )
    loaded = await load_private_inventory(
        f"/obs/root/delivery/{tail}", _DIGEST
    )
    assert loaded is inventory


async def test_publish_archive_builds_public_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication returns only the opaque public archive descriptor."""

    async def _build(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "archives/analyst.zip"

    async def _size(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        return 12

    monkeypatch.setattr(
        delivery_module,
        "build_and_publish_result_archive_with_runtime",
        _build,
    )
    monkeypatch.setattr(
        delivery_module, "_published_archive_size_with_runtime", _size
    )
    monkeypatch.setattr(
        delivery_module, "current_obs_runtime", lambda: object()
    )
    inventory = ResultArchiveInventory(
        run_root="/obs/root",
        members=(),
        digest=_DIGEST,
        total_size_bytes=0,
    )
    descriptor = await delivery_module._publish_archive(
        inventory, "analyst", "summary"
    )
    assert descriptor.name == "analyst-results.zip"
    assert descriptor.download_ref == f"result-archive:{_DIGEST}"


def test_begin_delivery_retry_requires_retryable_failure(
    tmp_path: Path,
) -> None:
    """Retry is refused when the stored result is not a failed delivery."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-1", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded", result={"execution": {}}),
    )
    assert begin_delivery_retry(registry, "run-1", owner="alice") is False


def _pending_result() -> dict[str, Any]:
    """Return one claimable pending delivery projection."""
    result = empty_execution_projection(result_archive_required=True)
    result["formatted"]["answer"] = "scientific answer"
    result["execution"]["delivery"]["inventory_digest"] = _DIGEST
    result["delivery_internal"] = {
        "inventory_ref": (
            f"/obs/root/delivery/{_DIGEST.removeprefix('sha256:')}/"
            ".phytomni-result-inventory.json"
        ),
        "attempts_claimed": 0,
        "last_error_code": None,
    }
    return result


async def test_worker_retries_archive_and_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retryable archive and OS errors sleep, then a later claim can stop."""
    inventory = ResultArchiveInventory(
        run_root="/obs/root",
        members=(),
        digest=_DIGEST,
        total_size_bytes=0,
    )

    async def _load(*args: Any, **kwargs: Any) -> ResultArchiveInventory:
        del args, kwargs
        return inventory

    monkeypatch.setattr(delivery_module, "load_private_inventory", _load)
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-1", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="running", result=_pending_result()),
    )
    calls = {"n": 0}

    def _publish(*args: Any) -> ResultArchiveDescriptor:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ResultArchiveError("archive_publish_failed", True)
        raise OSError("publish")

    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    await run_delivery_worker(
        registry,
        DeliveryRevision("run-1", "alice", 1, _DIGEST),
        delivery_module.ResultDeliveryDependencies(
            publish=_publish, sleep=_sleep
        ),
        "delivery:run-1:1",
    )
    assert calls["n"] >= 1
    assert sleeps
