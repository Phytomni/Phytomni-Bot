# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable, private archive-delivery state for report-producing runs."""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from ..config.defaults import ServerConfig
from ..mcp.formatting.models import ResultArchiveDescriptor, ResultDelivery
from ..runtime.outbound import current_obs_runtime
from ..storage.result_archive_storage import (
    load_result_archive_inventory_with_runtime,
)
from .live_tasks import deregister_live_task
from .result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    _published_archive_size_with_runtime,
    build_and_publish_result_archive_with_runtime,
)
from .run_registry_models import _now_iso
from .sqlite import sqlite_transaction
from .task_manager import _expires_at_for

if TYPE_CHECKING:
    from .run_registry import RunRegistry

__all__ = [
    "DeliveryFailure",
    "DeliveryRevision",
    "PrivateDeliveryState",
    "ResultArchivePublisher",
    "ResultDeliveryDependencies",
    "begin_delivery_retry",
    "claim_delivery_attempt",
    "default_result_delivery_dependencies",
    "delivery_attempts_exhausted",
    "delivery_backoff",
    "delivery_task_key",
    "initial_pending_delivery",
    "load_private_inventory",
    "mark_degraded_delivery_failure",
    "attach_public_delivery",
    "carry_execution_tasks",
    "carry_required_delivery",
    "failed_child_ids_from_result",
    "private_delivery_from_result",
    "replace_running_result",
    "result_delivery_from_result",
    "run_delivery_worker",
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


ResultArchivePublisher = Callable[
    [ResultArchiveInventory, str, str],
    ResultArchiveDescriptor | Awaitable[ResultArchiveDescriptor],
]
AsyncSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ResultDeliveryDependencies:
    """Injectable publisher and async delay for deterministic tests."""

    publish: ResultArchivePublisher
    sleep: AsyncSleep


@dataclass(frozen=True, slots=True)
class DeliveryAttemptClaim:
    """One durable attempt claim ready for out-of-transaction I/O."""

    inventory_ref: str
    agent: str
    summary_markdown: str
    attempts_claimed: int


@dataclass(frozen=True, slots=True)
class DeliveryRevision:
    """Owned immutable identity for one archive-delivery revision."""

    run_id: str
    owner: str
    revision: int
    inventory_digest: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.owner or not self.inventory_digest:
            raise ValueError("invalid delivery revision")
        if isinstance(self.revision, bool) or not isinstance(
            self.revision, int
        ):
            raise ValueError("invalid delivery revision")
        if self.revision < 1:
            raise ValueError("invalid delivery revision")


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    """Sanitized publication failure persisted for one delivery revision."""

    error_code: str
    retryable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.error_code, str) or not self.error_code:
            raise ValueError("invalid delivery failure")
        if not isinstance(self.retryable, bool):
            raise ValueError("invalid delivery failure")


def default_result_delivery_dependencies() -> ResultDeliveryDependencies:
    """Return production publication dependencies without test switches."""
    return ResultDeliveryDependencies(
        publish=_publish_archive, sleep=asyncio.sleep
    )


def delivery_task_key(run_id: str, revision: int) -> str:
    """Return the process-local identity for one delivery revision."""
    return f"delivery:{run_id}:{revision}"


def delivery_attempts_exhausted(attempts_claimed: int) -> bool:
    """Return whether the automatic publication budget is exhausted."""
    return attempts_claimed >= _MAX_AUTOMATIC_ATTEMPTS


def delivery_backoff(attempts_claimed: int) -> float:
    """Return the bounded delay after one durable failed attempt."""
    index = 0 if attempts_claimed <= 1 else 1
    return _BACKOFF_SECONDS[index]


def initial_pending_delivery(inventory_digest: str) -> ResultDelivery:
    """Build the canonical first pending delivery revision."""
    return ResultDelivery(
        schema_version=1,
        required=True,
        status="pending",
        revision=1,
        inventory_digest=inventory_digest,
        archive=None,
        error_code=None,
        retryable=False,
    )


def begin_delivery_retry(
    registry: RunRegistry, run_id: str, *, owner: str
) -> bool:
    """Advance a retryable terminal delivery to its next revision."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, result_json FROM runs "
            "WHERE run_id = ? AND user_id = ?",
            (run_id, owner),
        ).fetchone()
        if row is None or row[0] != "succeeded":
            return False
        result = _result_mapping(row[1])
        delivery = result_delivery_from_result(result)
        private = private_delivery_from_result(result)
        if not _retryable_failed_delivery(delivery, private):
            return False
        assert delivery is not None and private is not None
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
            "UPDATE runs SET status = 'running', result_json = ?, "
            "error = NULL, updated_at = ?, expires_at = NULL "
            "WHERE run_id = ? AND user_id = ? "
            "AND status = 'succeeded'",
            (json.dumps(result), _now_iso(), run_id, owner),
        )
        return cursor.rowcount == 1


def claim_delivery_attempt(
    registry: RunRegistry,
    target: DeliveryRevision,
) -> DeliveryAttemptClaim | None:
    """Durably claim one bounded automatic attempt before publication I/O."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, agent, result_json FROM runs "
            "WHERE run_id = ? AND user_id = ?",
            (target.run_id, target.owner),
        ).fetchone()
        if row is None or row[0] != "running":
            return None
        result = _result_mapping(row[2])
        delivery = result_delivery_from_result(result)
        private = private_delivery_from_result(result)
        if not _claimable_delivery(delivery, private, target):
            return None
        assert private is not None
        next_private = replace(
            private, attempts_claimed=private.attempts_claimed + 1
        )
        result[_DELIVERY_INTERNAL] = asdict(next_private)
        cursor = conn.execute(
            "UPDATE runs SET result_json = ?, updated_at = ? WHERE run_id = ? "
            "AND user_id = ? AND status = 'running'",
            (
                json.dumps(result),
                _now_iso(),
                target.run_id,
                target.owner,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return DeliveryAttemptClaim(
            inventory_ref=next_private.inventory_ref,
            agent=row[1],
            summary_markdown=_answer_from_result(result),
            attempts_claimed=next_private.attempts_claimed,
        )


async def run_delivery_worker(
    registry: RunRegistry,
    target: DeliveryRevision,
    dependencies: ResultDeliveryDependencies,
    task_key: str,
) -> None:
    """Claim, publish, and settle one immutable delivery revision."""
    try:
        while True:
            claim = claim_delivery_attempt(registry, target)
            if claim is None:
                return
            try:
                loaded_inventory = load_private_inventory(
                    claim.inventory_ref, target.inventory_digest
                )
                inventory = (
                    await loaded_inventory
                    if inspect.isawaitable(loaded_inventory)
                    else loaded_inventory
                )
                published = await asyncio.to_thread(
                    dependencies.publish,
                    inventory,
                    claim.agent,
                    claim.summary_markdown,
                )
                archive = (
                    await published
                    if inspect.isawaitable(published)
                    else published
                )
            except ResultArchiveError as exc:
                failure = DeliveryFailure(exc.code, exc.retryable)
            except (OSError, TypeError, ValueError):
                failure = DeliveryFailure("archive_publish_failed", True)
            else:
                settle_delivery_ready(registry, target, archive)
                return
            outcome = settle_delivery_failure(registry, target, failure)
            if outcome != "retry":
                return
            await dependencies.sleep(delivery_backoff(claim.attempts_claimed))
    finally:
        deregister_live_task(task_key)


def settle_delivery_failure(
    registry: RunRegistry,
    target: DeliveryRevision,
    failure: DeliveryFailure,
) -> Literal["retry", "failed", "stale"]:
    """Persist a failed claimed attempt, retaining the scientific answer."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT result_json FROM runs WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (target.run_id, target.owner),
        ).fetchone()
        if row is None:
            return "stale"
        result = _result_mapping(row[0])
        delivery = result_delivery_from_result(result)
        private = private_delivery_from_result(result)
        if not _delivery_matches_target(delivery, private, target):
            return "stale"
        assert delivery is not None and private is not None
        exhausted = delivery_attempts_exhausted(private.attempts_claimed)
        if failure.retryable and not exhausted:
            result[_DELIVERY_INTERNAL] = asdict(
                replace(private, last_error_code=failure.error_code)
            )
            conn.execute(
                "UPDATE runs SET result_json = ?, updated_at = ? "
                "WHERE run_id = ? "
                "AND user_id = ? AND status = 'running'",
                (
                    json.dumps(result),
                    _now_iso(),
                    target.run_id,
                    target.owner,
                ),
            )
            return "retry"
        failed_delivery = replace(
            delivery,
            status="failed",
            archive=None,
            error_code=failure.error_code,
            retryable=failure.retryable,
        )
        result = _replace_delivery(
            result,
            failed_delivery,
            replace(private, last_error_code=failure.error_code),
        )
        mark_degraded_delivery_failure(result, failure.retryable)
        now = _now_iso()
        conn.execute(
            "UPDATE runs SET status = 'succeeded', result_json = ?, "
            "error = NULL, "
            "updated_at = ?, expires_at = ? WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (
                json.dumps(result),
                now,
                _expires_at_for("succeeded", now),
                target.run_id,
                target.owner,
            ),
        )
        return "failed"


async def load_private_inventory(
    inventory_ref: str, inventory_digest: str
) -> ResultArchiveInventory:
    """Reload an immutable inventory reference with strict matching."""
    marker = "/delivery/"
    root, separator, tail = inventory_ref.rpartition(marker)
    expected = (
        f"{inventory_digest.removeprefix('sha256:')}/"
        ".phytomni-result-inventory.json"
    )
    if not root or not separator or tail != expected:
        raise ResultArchiveError("archive_contract_invalid")
    return await load_result_archive_inventory_with_runtime(
        root,
        inventory_digest,
        bucket=_CONFIG.BUCKET_NAME,
        obs_runtime=current_obs_runtime(),
    )


async def _publish_archive(
    inventory: ResultArchiveInventory,
    agent: str,
    summary_markdown: str,
) -> ResultArchiveDescriptor:
    """Publish the archive and return only an opaque public reference."""
    return await _publish_archive_with_runtime(
        inventory,
        agent,
        summary_markdown,
        obs_runtime=current_obs_runtime(),
    )


async def _publish_archive_with_runtime(
    inventory: ResultArchiveInventory,
    agent: str,
    summary_markdown: str,
    *,
    obs_runtime: Any,
) -> ResultArchiveDescriptor:
    """Publish one archive with separately scoped OBS SDK operations."""
    object_key = await build_and_publish_result_archive_with_runtime(
        inventory,
        agent=agent,
        summary_markdown=summary_markdown,
        obs_runtime=obs_runtime,
    )
    size_bytes = await _published_archive_size_with_runtime(
        _CONFIG.BUCKET_NAME,
        object_key,
        obs_runtime=obs_runtime,
    )
    return ResultArchiveDescriptor(
        role="result_archive",
        name=f"{agent}-results.zip",
        media_type="application/zip",
        size_bytes=size_bytes,
        downloadable=True,
        report_context_eligible=False,
        download_ref=f"result-archive:{inventory.digest}",
    )


def settle_delivery_ready(
    registry: RunRegistry,
    target: DeliveryRevision,
    archive: ResultArchiveDescriptor,
) -> bool:
    """Atomically complete a claimed revision only when it is still current."""
    with sqlite_transaction(registry.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT result_json FROM runs WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (target.run_id, target.owner),
        ).fetchone()
        if row is None:
            return False
        result = _result_mapping(row[0])
        delivery = result_delivery_from_result(result)
        private = private_delivery_from_result(result)
        if not _delivery_matches_target(delivery, private, target):
            return False
        assert delivery is not None and private is not None
        ready_delivery = replace(
            delivery,
            status="ready",
            archive=archive,
            error_code=None,
            retryable=False,
        )
        result = _replace_delivery(result, ready_delivery, private)
        now = _now_iso()
        cursor = conn.execute(
            "UPDATE runs SET status = 'succeeded', result_json = ?, "
            "error = NULL, "
            "updated_at = ?, expires_at = ? WHERE run_id = ? AND user_id = ? "
            "AND status = 'running'",
            (
                json.dumps(result),
                now,
                _expires_at_for("succeeded", now),
                target.run_id,
                target.owner,
            ),
        )
        return cursor.rowcount == 1


def _result_mapping(raw: object) -> dict[str, object]:
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return dict(result) if isinstance(result, Mapping) else {}


def _retryable_failed_delivery(
    delivery: ResultDelivery | None,
    private: PrivateDeliveryState | None,
) -> bool:
    """Return whether a terminal failure may start a new revision."""
    if delivery is None or private is None:
        return False
    if delivery.status != "failed" or not delivery.retryable:
        return False
    if delivery.archive is not None:
        return False
    return bool(delivery.inventory_digest)


def _delivery_matches_target(
    delivery: ResultDelivery | None,
    private: PrivateDeliveryState | None,
    target: DeliveryRevision,
) -> bool:
    """Return whether stored state still owns the requested revision."""
    if delivery is None or private is None:
        return False
    if delivery.status != "pending":
        return False
    if delivery.revision != target.revision:
        return False
    return delivery.inventory_digest == target.inventory_digest


def _claimable_delivery(
    delivery: ResultDelivery | None,
    private: PrivateDeliveryState | None,
    target: DeliveryRevision,
) -> bool:
    """Return whether one more automatic publication attempt may be claimed."""
    if not _delivery_matches_target(delivery, private, target):
        return False
    assert private is not None
    return not delivery_attempts_exhausted(private.attempts_claimed)


def attach_public_delivery(
    execution: Mapping[str, Any], projected: dict[str, Any]
) -> dict[str, Any]:
    """Copy a public archive-delivery block onto a projected execution."""
    parsed = result_delivery_from_result({"execution": execution})
    if parsed is None:
        return projected
    archive = None
    if parsed.archive is not None:
        archive = {
            "role": parsed.archive.role,
            "name": parsed.archive.name,
            "media_type": parsed.archive.media_type,
            "size_bytes": parsed.archive.size_bytes,
            "downloadable": parsed.archive.downloadable,
            "report_context_eligible": parsed.archive.report_context_eligible,
            "download_ref": parsed.archive.download_ref,
        }
    projected["delivery"] = {
        "schema_version": parsed.schema_version,
        "required": parsed.required,
        "status": parsed.status,
        "revision": parsed.revision,
        "inventory_digest": parsed.inventory_digest,
        "archive": archive,
        "error_code": parsed.error_code,
        "retryable": parsed.retryable,
    }
    return projected


def replace_running_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    owner: str,
    result: dict[str, Any],
) -> bool:
    """Write one running-row projection while carrying required delivery."""
    row = conn.execute(
        "SELECT result_json FROM runs "
        "WHERE run_id = ? AND user_id = ? AND status = 'running'",
        (run_id, owner),
    ).fetchone()
    if row is None:
        return False
    stored = json.loads(row[0]) if row[0] else None
    merged = carry_required_delivery(stored, result)
    merged = carry_execution_tasks(stored, merged)
    cursor = conn.execute(
        "UPDATE runs SET result_json = ?, updated_at = ? "
        "WHERE run_id = ? AND user_id = ? AND status = 'running'",
        (json.dumps(merged), _now_iso(), run_id, owner),
    )
    return cursor.rowcount == 1


def _execution_task_rows(result: object) -> list[dict[str, Any]]:
    """Return mutable copies of persisted execution.tasks mappings."""
    execution = (
        result.get("execution") if isinstance(result, Mapping) else None
    )
    tasks = execution.get("tasks") if isinstance(execution, Mapping) else None
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
        return []
    return [dict(item) for item in tasks if isinstance(item, Mapping)]


def failed_child_ids_from_result(result: object) -> tuple[str, ...]:
    """Return doomed child ids from a stored execution projection."""
    return tuple(
        item["id"]
        for item in _execution_task_rows(result)
        if item.get("accepted") is False
        and isinstance(item.get("id"), str)
        and item["id"]
    )


def _merge_execution_task_rows(
    stored_tasks: list[dict[str, Any]],
    incoming_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay live status onto stored five-key rows, then append unseen."""
    incoming_by_id: dict[str, Mapping[str, Any]] = {}
    for item in incoming_tasks:
        task_id = item.get("id")
        if isinstance(task_id, str) and task_id:
            incoming_by_id[task_id] = item
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stored in stored_tasks:
        row = dict(stored)
        task_id = row.get("id")
        if isinstance(task_id, str) and task_id in incoming_by_id:
            overlay = incoming_by_id[task_id]
            status = overlay.get("status")
            if isinstance(status, str) and status:
                row["status"] = status
            seen.add(task_id)
        merged.append(row)
    for item in incoming_tasks:
        task_id = item.get("id")
        if isinstance(task_id, str) and task_id and task_id not in seen:
            merged.append(dict(item))
    return merged


def carry_execution_tasks(
    stored_result: object,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Keep recorder five-key task rows when a formatted envelope is thinner.

    HTTP background workers replace the reserved projection with
    ``strip_agent_result`` of a formatted envelope whose ``execution.tasks``
    are often ``{id, accepted: True}`` rebuilt from ``task_ids``. Submit
    recording already stamped ``kind`` / ``error_code`` / doomed children;
    those rows must survive GetRun.
    """
    stored_tasks = _execution_task_rows(stored_result)
    if not stored_tasks:
        return incoming
    merged = _merge_execution_task_rows(
        stored_tasks, _execution_task_rows(incoming)
    )
    updated = dict(incoming)
    execution = updated.get("execution")
    next_execution = dict(execution) if isinstance(execution, Mapping) else {}
    next_execution["tasks"] = merged
    updated["execution"] = next_execution
    return updated


def carry_required_delivery(
    stored_result: object,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Keep a submit-time required delivery when the incoming one is absent.

    HTTP background workers replace the reserved run projection with a
    formatted tool envelope. That envelope serializes ``delivery: null``
    even after ``submit_recorder`` stamped ``required=true``. Harvest
    only builds the result archive when the stored marker survives.
    """
    if result_delivery_from_result(incoming) is not None:
        return incoming
    stored_delivery = result_delivery_from_result(stored_result)
    if stored_delivery is None:
        return incoming
    stored_execution = (
        stored_result.get("execution")
        if isinstance(stored_result, Mapping)
        else None
    )
    raw = (
        stored_execution.get("delivery")
        if isinstance(stored_execution, Mapping)
        else None
    )
    if not isinstance(raw, Mapping):
        return incoming
    updated = dict(incoming)
    execution = updated.get("execution")
    next_execution = dict(execution) if isinstance(execution, Mapping) else {}
    next_execution["delivery"] = dict(raw)
    updated["execution"] = next_execution
    if "delivery_internal" not in updated and isinstance(
        stored_result, Mapping
    ):
        private = stored_result.get(_DELIVERY_INTERNAL)
        if isinstance(private, Mapping):
            updated[_DELIVERY_INTERNAL] = dict(private)
    return updated


def result_delivery_from_result(result: object) -> ResultDelivery | None:
    """Parse the canonical public delivery block from a stored result."""
    if not isinstance(result, Mapping):
        return None
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
        # The dataclass validates every dynamic JSON value in __post_init__;
        # these casts only bridge that checked runtime boundary for type tools.
        return ResultDelivery(
            schema_version=cast(Literal[1], raw.get("schema_version")),
            required=cast(bool, raw.get("required")),
            status=cast(
                Literal["pending", "ready", "failed"],
                raw.get("status"),
            ),
            revision=cast(int, raw.get("revision")),
            inventory_digest=cast(str, raw.get("inventory_digest")),
            archive=archive,
            error_code=raw.get("error_code"),
            retryable=cast(bool, raw.get("retryable")),
        )
    except (TypeError, ValueError):
        return None


def private_delivery_from_result(
    result: object,
) -> PrivateDeliveryState | None:
    """Parse bounded private coordination state from a stored result."""
    if not isinstance(result, Mapping):
        return None
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


def mark_degraded_delivery_failure(
    result: dict[str, object], retryable: bool
) -> None:
    """Expose a delivery-only failure without replacing scientific output."""
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return
    execution["tracking"] = {"degraded": True}
    warnings = execution.get("warnings")
    values = list(warnings) if isinstance(warnings, list) else []
    values = [
        item
        for item in values
        if not (
            isinstance(item, Mapping) and item.get("code") == _DELIVERY_WARNING
        )
    ]
    values.append(
        {
            "code": _DELIVERY_WARNING,
            "retryable": retryable,
            "stage": "result_delivery",
        }
    )
    execution["warnings"] = values
