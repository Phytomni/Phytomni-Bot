# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Schema constants and storage-neutral models for the run registry."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..agents.shared.a2ui.validation import (
    A2uiSurfaceValidationError,
    validate_a2ui_surface,
)
from .locale import SUPPORTED_LOCALES, SupportedLocale
from .research_failure_codes import (
    RESEARCH_FAILURE_CODES,
)
from .research_failure_codes import (
    ResearchFailureCode as _ResearchFailureCode,
)

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_FAILURE_STATUSES = frozenset({"failed", "error"})
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_NON_POLLABLE_RUN_STATUSES = _TERMINAL_RUN_STATUSES | {"input_required"}
ResearchFailureCode = _ResearchFailureCode
RESEARCH_FAILURE_MESSAGES = {
    code: "Research request could not be completed."
    for code in RESEARCH_FAILURE_CODES
}
RESEARCH_FAILURE_CONTRACTS: dict[str, frozenset[tuple[int, bool, str]]] = {
    "research_idempotency_key_required": frozenset(
        {(400, False, "input_resolution")}
    ),
    "research_idempotency_conflict": frozenset(
        {(409, False, "input_resolution")}
    ),
    "research_data_block_invalid": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_dataset_path_invalid": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_dataset_not_found": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_dataset_duplicate": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_dataset_format_unsupported": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_input_limit_exceeded": frozenset(
        {(413, False, "input_resolution")}
    ),
    "research_document_extraction_failed": frozenset(
        {
            (422, False, "input_resolution"),
            (503, True, "input_resolution"),
        }
    ),
    "research_input_resolution_failed": frozenset(
        {(422, False, "input_resolution")}
    ),
    "research_input_resolution_unavailable": frozenset(
        {
            (503, True, "input_resolution"),
            (503, False, "input_resolution"),
        }
    ),
    "research_run_tracking_failed": frozenset({(502, True, "execution")}),
    "research_input_protocol_unavailable": frozenset(
        {(503, True, "input_resolution")}
    ),
    "research_cancel_conflict": frozenset({(409, False, "execution")}),
}


def research_failure_contract_values(
    code: str, failure: Mapping[str, object]
) -> dict[str, object] | None:
    """Return validated private fields for one stable public tuple."""
    expected = RESEARCH_FAILURE_CONTRACTS.get(code)
    stage = failure.get("stage")
    retryable = failure.get("retryable")
    status_hint = failure.get("http_status_hint")
    values = (status_hint, retryable, stage)
    if (
        expected is None
        or tuple(type(value) for value in values) != (int, bool, str)
        or values not in expected
    ):
        return None
    return {
        "stage": stage,
        "retryable": retryable,
        "http_status_hint": status_hint,
    }


_CREATE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    dialogue_id TEXT,
    request_id TEXT,
    query TEXT,
    tool_name TEXT,
    model TEXT,
    request_json TEXT,
    locale TEXT,
    a2a_task_id TEXT,
    a2a_context_id TEXT,
    a2a_message_id TEXT,
    stage TEXT,
    failure_json TEXT,
    revision INTEGER NOT NULL DEFAULT 0
)
"""

_REQUEST_INFO_COLUMNS = (
    ("dialogue_id", "TEXT"),
    ("request_id", "TEXT"),
    ("query", "TEXT"),
    ("tool_name", "TEXT"),
    ("model", "TEXT"),
    ("request_json", "TEXT"),
    ("locale", "TEXT"),
)
_A2A_COLUMNS = (
    ("a2a_task_id", "TEXT"),
    ("a2a_context_id", "TEXT"),
    ("a2a_message_id", "TEXT"),
)
_RESEARCH_COORDINATOR_COLUMNS = (
    ("stage", "TEXT"),
    ("failure_json", "TEXT"),
    ("revision", "INTEGER NOT NULL DEFAULT 0"),
)
_CREATE_RUNS_USER_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)"
)
_CREATE_TASKS_RUN_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id)"
)
_CREATE_A2A_TASK_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runs_a2a_task_user "
    "ON runs(a2a_task_id, user_id)"
)
_CREATE_A2UI_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS run_a2ui_actions (
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    surface_id TEXT NOT NULL,
    widget TEXT NOT NULL,
    action_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    outcome TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (run_id, surface_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""
_CREATE_A2UI_OWNER_ACTION_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_run_a2ui_actions_owner_action "
    "ON run_a2ui_actions(user_id, action_id)"
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _surface_identity_from_result(
    result_json: str | None,
) -> tuple[str, str]:
    """Extract and validate the open A2UI surface from persisted JSON."""
    try:
        result = json.loads(result_json or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise A2UIActionInvariantError(
            "persisted input request is not valid JSON"
        ) from exc
    if not isinstance(result, Mapping):
        raise A2UIActionInvariantError(
            "persisted input request result is not an object"
        )
    interrupt = result.get("interrupt")
    draft = interrupt.get("draft") if isinstance(interrupt, Mapping) else None
    surface = draft.get("a2ui") if isinstance(draft, Mapping) else None
    if not isinstance(surface, Mapping):
        raise A2UIActionInvariantError(
            "persisted input request has no A2UI surface"
        )
    try:
        validated = validate_a2ui_surface(surface)
    except (A2uiSurfaceValidationError, TypeError, ValueError) as exc:
        raise A2UIActionInvariantError(
            "persisted input request has an invalid A2UI surface"
        ) from exc
    return validated.surface_id, validated.widget


def _aggregate_status(task_statuses: list[str]) -> str:
    """Aggregate child task statuses into one run status."""
    lowered = [status.lower() for status in task_statuses if status]
    if any(status in _FAILURE_STATUSES for status in lowered):
        return "failed"
    if lowered and all(status in _SUCCESS_STATUSES for status in lowered):
        return "succeeded"
    return "running"


@dataclass(frozen=True)
class RunSpec:
    """Identity of one run row."""

    run_id: str
    user_id: str
    agent: str
    origin: str


def local_run_spec(run_id: str, user_id: str, agent: str) -> RunSpec:
    """Build the standard local-origin run identity."""
    return RunSpec(
        run_id=run_id,
        user_id=user_id,
        agent=agent,
        origin="local",
    )


@dataclass(frozen=True)
class A2ACorrelation:
    """Protocol ids linking one A2A task to an existing run row."""

    task_id: str | None = None
    context_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class A2UIActionClaim:
    """Identity of the first uplink accepted for one paused surface."""

    run_id: str
    surface_id: str
    widget: str
    action_id: str
    channel: str


@dataclass(frozen=True, slots=True)
class A2UIActionIdentity:
    """Stable identity fields shared by a claim and its audit row."""

    run_id: str
    surface_id: str
    widget: str
    action_id: str


@dataclass(frozen=True, slots=True)
class A2UIActionAudit:
    """Safe audit projection for one claimed A2UI action."""

    identity: A2UIActionIdentity
    channel: str
    outcome: str
    claimed_at: str
    completed_at: str | None

    @property
    def run_id(self) -> str:
        """Return the audited run id."""
        return self.identity.run_id

    @property
    def surface_id(self) -> str:
        """Return the audited surface id."""
        return self.identity.surface_id

    @property
    def widget(self) -> str:
        """Return the audited widget kind."""
        return self.identity.widget

    @property
    def action_id(self) -> str:
        """Return the audited action id."""
        return self.identity.action_id


class A2UIActionConflictError(RuntimeError):
    """Raised when the current pause is absent, mismatched, or claimed."""


A2UIActionConflict = A2UIActionConflictError


class A2UIActionInvariantError(RuntimeError):
    """Raised when persisted input-required data violates its invariant."""


@dataclass(frozen=True)
class RunOutcome:
    """Initial outcome state of a newly-created run row."""

    status: str = "running"
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _RequestIdentity:
    """Correlate the request with an optional dialogue identifier."""

    dialogue_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunRequestInfo:
    """Per-request metadata persisted alongside the run row."""

    _identity: _RequestIdentity = field(init=False, repr=False)
    query: str | None = None
    tool_name: str | None = None
    model: str | None = None
    request_json: str | None = None
    locale: SupportedLocale | None = None
    a2a: A2ACorrelation = A2ACorrelation()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build metadata while retaining the legacy constructor contract."""
        names = (
            "dialogue_id",
            "request_id",
            "query",
            "tool_name",
            "model",
            "request_json",
            "locale",
            "a2a",
        )
        if len(args) > len(names):
            raise TypeError(
                f"RunRequestInfo expected at most {len(names)} arguments"
            )
        values: dict[str, Any] = dict(zip(names, args))
        identity = kwargs.pop("_identity", None)
        for name, value in kwargs.items():
            if name not in names:
                raise TypeError(
                    f"RunRequestInfo got an unexpected keyword argument "
                    f"{name!r}"
                )
            if name in values:
                raise TypeError(
                    f"RunRequestInfo got multiple values for argument "
                    f"{name!r}"
                )
            values[name] = value
        if identity is None:
            identity = _RequestIdentity(
                values.get("dialogue_id"),
                values.get("request_id"),
            )
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "query", values.get("query"))
        object.__setattr__(self, "tool_name", values.get("tool_name"))
        object.__setattr__(self, "model", values.get("model"))
        object.__setattr__(self, "request_json", values.get("request_json"))
        object.__setattr__(self, "locale", values.get("locale"))
        object.__setattr__(
            self,
            "a2a",
            values.get("a2a", A2ACorrelation()),
        )
        self.__post_init__()

    @property
    def dialogue_id(self) -> str | None:
        """Return the legacy dialogue correlation field."""
        return self._identity.dialogue_id

    @property
    def request_id(self) -> str | None:
        """Return the HTTP request correlation field."""
        return self._identity.request_id

    def __post_init__(self) -> None:
        """Reject unsupported values when hydrating persisted metadata."""
        if self.locale is not None and self.locale not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported persisted locale: {self.locale}")


@dataclass(frozen=True)
class RunFilter:
    """Optional list-time filters for ``list_runs``."""

    status: str | None = None
    agent: str | None = None
    origin: str | None = None
    dialogue_id: str | None = None
    created_after: str | None = None
    created_before: str | None = None


@dataclass(frozen=True)
class Timestamps:
    """Row timestamps and TTL expiry for one run."""

    created_at: str
    updated_at: str
    expires_at: str | None


@dataclass(frozen=True)
class _RunRecordCore:
    """Read view of one run row plus its child task ids."""

    spec: RunSpec
    status: str
    result: dict[str, Any] | None
    error: str | None
    timestamps: Timestamps
    task_ids: tuple[str, ...]
    request_info: RunRequestInfo = RunRequestInfo()


@dataclass(frozen=True)
class RunRecord(_RunRecordCore):
    """Read view of one run row plus public coordinator state."""

    stage: str | None = None
    failure: dict[str, Any] | None = None
    revision: int = 0

    @property
    def a2a(self) -> A2ACorrelation:
        """Return the protocol correlation nested in request metadata."""
        return self.request_info.a2a
