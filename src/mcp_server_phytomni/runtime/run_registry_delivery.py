# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Durable, private archive-delivery state for report-producing runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

from ..config.defaults import ServerConfig
from ..mcp.formatting.models import ResultArchiveDescriptor, ResultDelivery
from ..storage.obs_relay_ops import object_size
from ..storage.result_archive_storage import load_result_archive_inventory
from .result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    build_and_publish_result_archive,
)
from .run_registry_models import _now_iso
from .sqlite import sqlite_transaction
from .task_manager import _expires_at_for

if TYPE_CHECKING:
    from .run_registry import RunRegistry

__all__ = [
    "PrivateDeliveryState",
    "ResultArchivePublisher",
    "ResultDeliveryDependencies",
    "begin_delivery_retry",
    "claim_delivery_attempt",
    "default_result_delivery_dependencies",
    "delivery_task_key",
    "load_private_inventory",
    "settle_delivery_failure",
    "settle_delivery_ready",
]

_DELIVERY_INTERNAL = "delivery_internal"
_MAX_AUTOMATIC_ATTEMPTS = 3
_DELIVERY_WARNING = "result_archive_delivery_failed"
_BACKOFF_SECONDS = (0.05, 0.1)
_CONFIG = ServerConfig()


@dataclass(frozen=True, slots=True)
class PrivateDeliveryState:
    """Private coordination state that must never cross a public boundary."""

    inventory_ref: str
    attempts_claimed: int
    last_error_code: str | None

    def __post_init__(self) -> None:
        if (
            not self.inventory_ref
            or self.attempts_claimed < 0
            or self.attempts_claimed > _MAX_AUTOMATIC_ATTEMPTS
        ):
            raise ValueError("invalid private delivery state")


class ResultArchivePublisher(Protocol):
    """Publish one immutable archive and return its safe public descriptor."""

    def __call__(
        self,
        inventory: ResultArchiveInventory,
        *,
        agent: str,
        summary_markdown: str,
    ) -> ResultArchiveDescriptor: ...


AsyncSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ResultDeliveryDependencies:
    """Injectable blocking publisher and async delay for deterministic tests."""

    publish: ResultArchivePublisher
    sleep: AsyncSleep


@dataclass(frozen=True, slots=True)
class DeliveryAttemptClaim:
    """One durable attempt claim ready for out-of-transaction I/O."""

    inventory_ref: str
    agent: str
    summary_markdown: str
    attempts_claimed: int


def default_result_delivery_dependencies() -> ResultDeliveryDependencies:
    """Return production publication dependencies without test switches."""
    return ResultDeliveryDependencies(publish=_publish_archive, sleep=asyncio.sleep)


def delivery_task_key(run_id: str, revision: int) -> str:
    """Return the process-local identity for one delivery revision."""
    return f"delivery:{run_id}:{revision}"


def begin_delivery_retry(registry: RunRegistry, run_id: str, *, owner: str) -> bool:
    """Advance one retryable terminal delivery to its next immutable revision."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, result_json FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, owner),
        ).fetchone()
        if row is None or row[0] != "succeeded":
            return False
        result = _result_mapping(row[1])
        delivery = _delivery_from_result(result)
        private = _private_from_result(result)
        if (
            delivery is None
            or private is None
            or delivery.status != "failed"
            or not delivery.retryable
            or delivery.archive is not None
            or delivery.inventory_digest == ""
        ):
            return False
        next_delivery = replace(
            delivery,
            status="pending",
            revision=delivery.revision + 1,
            archive=None,
            error_code=None,
            retryable=False,
        )
        result = _replace_delivery(
            result,
            next_delivery,
            PrivateDeliveryState(
                inventory_ref=private.inventory_ref,
                attempts_claimed=0,
                last_error_code=None,
            ),
        )
        cursor = conn.execute(
            "UPDATE runs SET status = 'running', result_json = ?, error = NULL, "
            "updated_at = ?, expires_at = NULL WHERE run_id = ? AND user_id = ? "
            "AND status = 'succeeded'",
            (json.dumps(result), _now_iso(), run_id, owner),
        )
        return cursor.rowcount == 1


def claim_delivery_attempt(
    registry: RunRegistry,
    run_id: str,
    *,
    owner: str,
    revision: int,
    inventory_digest: str,
) -> DeliveryAttemptClaim | None:
    """Durably claim one bounded automatic attempt before publication I/O."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, agent, result_json FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, owner),
        ).fetchone()
        if row is None or row[0] != "running":
            return None
        result = _result_mapping(row[2])
        delivery = _delivery_from_result(result)
        private = _private_from_result(result)
        if (
            delivery is None
            or private is None
            or delivery.status != "pending"
            or delivery.revision != revision
            or delivery.inventory_digest != inventory_digest
            or private.attempts_claimed >= _MAX_AUTOMATIC_ATTEMPTS
        ):
            return None
        next_private = replace(
            private, attempts_claimed=private.attempts_claimed + 1
        )
        result[_DELIVERY_INTERNAL] = asdict(next_private)
        cursor = conn.execute(
            "UPDATE runs SET result_json = ?, updated_at = ? WHERE run_id = ? "
            "AND user_id = ? AND status = 'running'",
            (json.dumps(result), _now_iso(), run_id, owner),
        )
        if cursor.rowcount != 1:
            return None
        return DeliveryAttemptClaim(
            inventory_ref=next_private.inventory_ref,
            agent=row[1],
            summary_markdown=_answer_from_result(result),
            attempts_claimed=next_private.attempts_claimed,
        )


def settle_delivery_ready(
    registry: RunRegistry,
    run_id: str,
    *,
    owner: str,
    revision: int,
    inventory_digest: str,
    archive: ResultArchiveDescriptor,
) -> bool:
    """Atomically complete a claimed revision only when it is still current."""
    return _settle_delivery(
        registry,
        run_id,
        owner=owner,
        revision=revision,
        inventory_digest=inventory_digest,
        archive=archive,
        error_code=None,
        retryable=False,
    )


def settle_delivery_failure(
    registry: RunRegistry,
    run_id: str,
    *,
    owner: str,
    revision: int,
    inventory_digest: str,
    error_code: str,
    retryable: bool,
) -> Literal["retry", "failed", "stale"]:
    """Persist a failed claimed attempt, retaining the scientific answer."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT result_json FROM runs WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (run_id, owner),
        ).fetchone()
        if row is None:
            return "stale"
        result = _result_mapping(row[0])
        delivery = _delivery_from_result(result)
        private = _private_from_result(result)
        if (
            delivery is None
            or private is None
            or delivery.status != "pending"
            or delivery.revision != revision
            or delivery.inventory_digest != inventory_digest
        ):
            return "stale"
        exhausted = private.attempts_claimed >= _MAX_AUTOMATIC_ATTEMPTS
        if retryable and not exhausted:
            result[_DELIVERY_INTERNAL] = asdict(
                replace(private, last_error_code=error_code)
            )
            conn.execute(
                "UPDATE runs SET result_json = ?, updated_at = ? WHERE run_id = ? "
                "AND user_id = ? AND status = 'running'",
                (json.dumps(result), _now_iso(), run_id, owner),
            )
            return "retry"
        failed_delivery = replace(
            delivery,
            status="failed",
            archive=None,
            error_code=error_code,
            retryable=retryable,
        )
        result = _replace_delivery(
            result,
            failed_delivery,
            replace(private, last_error_code=error_code),
        )
        _mark_degraded_delivery_failure(result, retryable)
        now = _now_iso()
        conn.execute(
            "UPDATE runs SET status = 'succeeded', result_json = ?, error = NULL, "
            "updated_at = ?, expires_at = ? WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (
                json.dumps(result),
                now,
                _expires_at_for("succeeded", now),
                run_id,
                owner,
            ),
        )
        return "failed"


def load_private_inventory(
    inventory_ref: str, inventory_digest: str
) -> ResultArchiveInventory:
    """Reload the immutable private inventory reference with strict matching."""
    marker = "/delivery/"
    root, separator, tail = inventory_ref.rpartition(marker)
    expected = (
        f"{inventory_digest.removeprefix('sha256:')}/"
        ".phytomni-result-inventory.json"
    )
    if not root or not separator or tail != expected:
        raise ResultArchiveError("archive_contract_invalid")
    return load_result_archive_inventory(
        root,
        inventory_digest,
        bucket=_CONFIG.BUCKET_NAME,
        obs_server=_CONFIG.OBS_SERVER,
    )


def _publish_archive(
    inventory: ResultArchiveInventory,
    *,
    agent: str,
    summary_markdown: str,
) -> ResultArchiveDescriptor:
    """Publish through Task 3 and return only an opaque public reference."""
    object_key = build_and_publish_result_archive(
        inventory, agent=agent, summary_markdown=summary_markdown
    )
    try:
        size_bytes = object_size(
            _CONFIG.BUCKET_NAME, object_key, obs_server=_CONFIG.OBS_SERVER
        )
    except OSError:
        raise ResultArchiveError("archive_publish_failed", retryable=True) from None
    if size_bytes is None:
        raise ResultArchiveError("archive_publish_failed", retryable=True)
    return ResultArchiveDescriptor(
        role="result_archive",
        name=f"{agent}-results.zip",
        media_type="application/zip",
        size_bytes=size_bytes,
        downloadable=True,
        report_context_eligible=False,
        download_ref=f"result-archive:{inventory.digest}",
    )


def _settle_delivery(
    registry: RunRegistry,
    run_id: str,
    *,
    owner: str,
    revision: int,
    inventory_digest: str,
    archive: ResultArchiveDescriptor,
    error_code: None,
    retryable: bool,
) -> bool:
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT result_json FROM runs WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (run_id, owner),
        ).fetchone()
        if row is None:
            return False
        result = _result_mapping(row[0])
        delivery = _delivery_from_result(result)
        private = _private_from_result(result)
        if (
            delivery is None
            or private is None
            or delivery.status != "pending"
            or delivery.revision != revision
            or delivery.inventory_digest != inventory_digest
        ):
            return False
        ready_delivery = replace(
            delivery,
            status="ready",
            archive=archive,
            error_code=error_code,
            retryable=retryable,
        )
        result = _replace_delivery(result, ready_delivery, private)
        now = _now_iso()
        cursor = conn.execute(
            "UPDATE runs SET status = 'succeeded', result_json = ?, error = NULL, "
            "updated_at = ?, expires_at = ? WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (
                json.dumps(result),
                now,
                _expires_at_for("succeeded", now),
                run_id,
                owner,
            ),
        )
        return cursor.rowcount == 1


def _result_mapping(raw: object) -> dict[str, object]:
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return dict(result) if isinstance(result, Mapping) else {}


def _delivery_from_result(result: Mapping[str, object]) -> ResultDelivery | None:
    execution = result.get("execution")
    raw = execution.get("delivery") if isinstance(execution, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        archive_raw = raw.get("archive")
        archive = (
            ResultArchiveDescriptor(**dict(archive_raw))
            if isinstance(archive_raw, Mapping)
            else None
        )
        return ResultDelivery(
            schema_version=raw.get("schema_version"),
            required=raw.get("required"),
            status=raw.get("status"),
            revision=raw.get("revision"),
            inventory_digest=raw.get("inventory_digest"),
            archive=archive,
            error_code=raw.get("error_code"),
            retryable=raw.get("retryable"),
        )
    except (TypeError, ValueError):
        return None


def _private_from_result(result: Mapping[str, object]) -> PrivateDeliveryState | None:
    raw = result.get(_DELIVERY_INTERNAL)
    if not isinstance(raw, Mapping):
        return None
    try:
        return PrivateDeliveryState(**dict(raw))
    except (TypeError, ValueError):
        return None


def _replace_delivery(
    result: Mapping[str, object],
    delivery: ResultDelivery,
    private: PrivateDeliveryState,
) -> dict[str, object]:
    updated = dict(result)
    execution = updated.get("execution")
    next_execution = dict(execution) if isinstance(execution, Mapping) else {}
    next_execution["delivery"] = asdict(delivery)
    updated["execution"] = next_execution
    updated[_DELIVERY_INTERNAL] = asdict(private)
    return updated


def _answer_from_result(result: Mapping[str, object]) -> str:
    formatted = result.get("formatted")
    answer = formatted.get("answer") if isinstance(formatted, Mapping) else ""
    return answer if isinstance(answer, str) else ""


def _mark_degraded_delivery_failure(
    result: dict[str, object], retryable: bool
) -> None:
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return
    execution["tracking"] = {"degraded": True}
    warnings = execution.get("warnings")
    values = list(warnings) if isinstance(warnings, list) else []
    values = [
        item
        for item in values
        if not (isinstance(item, Mapping) and item.get("code") == _DELIVERY_WARNING)
    ]
    values.append(
        {
            "code": _DELIVERY_WARNING,
            "retryable": retryable,
            "stage": "result_delivery",
        }
    )
    execution["warnings"] = values
