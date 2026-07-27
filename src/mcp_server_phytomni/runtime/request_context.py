# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Per-request identity and recorder-state context.

Functions enumerated in ``__all__``. MCP stdio binds none of these
(getters return their defaults); the run-id slot carries the submit
chokepoint's freshly-minted run_id forward to the HTTP response
builder. Accepted task ids preserve upstream submission identity even
when local persistence fails, while the recorder-degraded slot marks
that failure for the HTTP response.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from .locale import SupportedLocale, bind_effective_locale

__all__ = [
    "bind_accepted_task_ids",
    "bind_recorder_degraded",
    "bind_request_id",
    "bind_request_user",
    "bind_pre_recorded_task_id",
    "bind_run_id",
    "current_accepted_task_ids",
    "current_recorder_degraded",
    "current_request_id",
    "current_request_user",
    "current_pre_recorded_task_id",
    "current_run_id",
    "request_context",
    "reset_request_var",
]

_request_user: ContextVar[str | None] = ContextVar(
    "phytomni_request_user", default=None
)
_request_id: ContextVar[str | None] = ContextVar(
    "phytomni_request_id", default=None
)
_request_run_id: ContextVar[str | None] = ContextVar(
    "phytomni_request_run_id", default=None
)
_pre_recorded_task_id: ContextVar[str | None] = ContextVar(
    "phytomni_pre_recorded_task_id", default=None
)
_recorder_degraded: ContextVar[bool] = ContextVar(
    "phytomni_recorder_degraded", default=False
)
_accepted_task_ids: ContextVar[tuple[str, ...]] = ContextVar(
    "phytomni_accepted_task_ids", default=()
)
_request_stage_trace: ContextVar[tuple[Any, ...]] = ContextVar(
    "phytomni_stage_trace", default=()
)


def current_request_user() -> str | None:
    """Return the authenticated user id bound to this request, if any."""
    return _request_user.get()


def current_request_id() -> str | None:
    """Return the correlation id bound to this request, if any."""
    return _request_id.get()


def current_run_id() -> str | None:
    """Return the run id bound to this request by the chokepoint.

    The submit chokepoint in ``mcp/handlers`` calls ``bind_run_id``
    after a successful ``RunRegistry.create_run`` so the HTTP layer
    can recover the freshly-minted run id without reading any
    formatter-specific metadata.
    """
    return _request_run_id.get()


def current_pre_recorded_task_id() -> str | None:
    """Return the DeepGenome task reserved before generic recording."""
    return _pre_recorded_task_id.get()


def current_accepted_task_ids() -> tuple[str, ...]:
    """Return accepted upstream task ids for the current request."""
    return _accepted_task_ids.get()


def bind_request_user(user_id: str | None) -> Token[str | None]:
    """Bind the request user id and return a reset token."""
    return _request_user.set(user_id)


def bind_request_id(request_id: str | None) -> Token[str | None]:
    """Bind the request correlation id and return a reset token."""
    return _request_id.set(request_id)


def bind_run_id(run_id: str | None) -> Token[str | None]:
    """Bind the chokepoint-minted run id and return a reset token.

    The HTTP path's ``request_context_middleware`` brackets this
    contextvar with ``None`` at the start of every request so a value
    written by one handler never leaks into the next request.
    """
    return _request_run_id.set(run_id)


def bind_pre_recorded_task_id(task_id: str | None) -> Token[str | None]:
    """Bind the task id already persisted by a submit agent."""
    return _pre_recorded_task_id.set(task_id)


def bind_accepted_task_ids(
    task_ids: tuple[str, ...],
) -> Token[tuple[str, ...]]:
    """Bind de-duplicated accepted task ids and return a reset token."""
    clean = tuple(dict.fromkeys(value for value in task_ids if value.strip()))
    return _accepted_task_ids.set(clean)


def _bind_stage_trace(events: tuple[Any, ...] = ()) -> Token[tuple[Any, ...]]:
    """Bind the untyped backing tuple used by the stage-trace module."""
    return _request_stage_trace.set(events)


def _current_stage_trace() -> tuple[Any, ...]:
    """Return the backing stage-trace tuple without a runtime import cycle."""
    return _request_stage_trace.get()


def _append_request_stage_event(event: Any) -> None:
    """Append one immutable stage event to the current request context."""
    _request_stage_trace.set((*_request_stage_trace.get(), event))


def current_recorder_degraded() -> bool:
    """Return whether the submit chokepoint hit a persistence failure.

    The submit chokepoint in ``runtime/submit_recorder`` writes its
    runs / tasks rows best-effort: a ``sqlite3.Error`` / ``OSError``
    during the registry write must not break an already-successful
    remote submission, but it leaves the run un-tracked locally
    (``current_run_id() is None``, ``RunRegistry.get_run`` returns
    ``None``) so a client polling ``GET /v1/runs/{id}`` would see a
    permanent ``404``. Setting this flag lets the HTTP layer mark the
    202 body ``degraded_tracking: true`` while preserving the accepted
    upstream task ids, even though no durable local run id is available.
    """
    return _recorder_degraded.get()


def bind_recorder_degraded(degraded: bool) -> Token[bool]:
    """Mark the request's recorder state and return a reset token.

    The HTTP path's ``request_context()`` brackets this contextvar
    with ``False`` at the start of every request, so a value set by
    one handler never leaks into the next request.
    """
    return _recorder_degraded.set(degraded)


def reset_request_var(token: Token[Any]) -> None:
    """Reset a request contextvar to its prior value.

    Args:
        token: A token returned by one of the request-context bind
            functions.
    """
    token.var.reset(token)


@contextmanager
def request_context(
    user_id: str | None,
    request_id: str | None,
    run_id: str | None = None,
    *,
    locale: SupportedLocale | None = None,
) -> Generator[None, None, None]:
    """Bind request identity and locale for the duration of the block.

    Args:
        user_id: Authenticated user id, or None.
        request_id: Correlation id, or None.
        run_id: Submit chokepoint's run id, or None. Defaults to
            ``None`` since most callers (test harnesses, the HTTP
            middleware entry) want the run id to be discovered later
            by the chokepoint inside the block.
        locale: Effective natural-language locale. Defaults to ``en-US``
            for legacy callers that resolve locale at a later ingress.

    Yields:
        None while all request contextvars are bound. Accepted task ids
        are seeded to an empty tuple and the
        recorder-degraded flag is always seeded to ``False`` at entry
        so a chokepoint failure can only flag the current request,
        never inherit a stale ``True`` from an earlier one.
    """
    user_token = bind_request_user(user_id)
    id_token = bind_request_id(request_id)
    run_token = bind_run_id(run_id)
    locale_token = bind_effective_locale(locale or "en-US")
    pre_recorded_token = bind_pre_recorded_task_id(None)
    degraded_token = bind_recorder_degraded(False)
    accepted_task_ids_token = bind_accepted_task_ids(())
    stage_trace_token = _bind_stage_trace()
    try:
        yield
    finally:
        reset_request_var(stage_trace_token)
        reset_request_var(accepted_task_ids_token)
        reset_request_var(degraded_token)
        reset_request_var(pre_recorded_token)
        reset_request_var(locale_token)
        reset_request_var(run_token)
        reset_request_var(id_token)
        reset_request_var(user_token)
