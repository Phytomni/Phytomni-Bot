# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Per-request user, request-id, run-id, and recorder-state context.

Functions enumerated in ``__all__``. MCP stdio binds none of these
(getters return their defaults); the run-id slot carries the submit
chokepoint's freshly-minted run_id forward to the HTTP response
builder, and the recorder-degraded slot signals a silent local-
registry persistence failure so the HTTP body distinguishes it from
the legitimate analyst dedup-hit ``id=None`` / ``task_ids=[]``.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Optional

__all__ = [
    "bind_recorder_degraded",
    "bind_request_id",
    "bind_request_user",
    "bind_run_id",
    "current_recorder_degraded",
    "current_request_id",
    "current_request_user",
    "current_run_id",
    "request_context",
    "reset_request_var",
]

_request_user: ContextVar[Optional[str]] = ContextVar(
    "phytomni_request_user", default=None
)
_request_id: ContextVar[Optional[str]] = ContextVar(
    "phytomni_request_id", default=None
)
_request_run_id: ContextVar[Optional[str]] = ContextVar(
    "phytomni_request_run_id", default=None
)
_recorder_degraded: ContextVar[bool] = ContextVar(
    "phytomni_recorder_degraded", default=False
)


def current_request_user() -> Optional[str]:
    """Return the authenticated user id bound to this request, if any."""
    return _request_user.get()


def current_request_id() -> Optional[str]:
    """Return the correlation id bound to this request, if any."""
    return _request_id.get()


def current_run_id() -> Optional[str]:
    """Return the run id bound to this request by the chokepoint.

    The submit chokepoint in ``mcp/handlers`` calls ``bind_run_id``
    after a successful ``RunRegistry.create_run`` so the HTTP layer
    can recover the freshly-minted run id without reading any
    formatter-specific metadata.
    """
    return _request_run_id.get()


def bind_request_user(user_id: Optional[str]) -> Token[Optional[str]]:
    """Bind the request user id and return a reset token."""
    return _request_user.set(user_id)


def bind_request_id(request_id: Optional[str]) -> Token[Optional[str]]:
    """Bind the request correlation id and return a reset token."""
    return _request_id.set(request_id)


def bind_run_id(run_id: Optional[str]) -> Token[Optional[str]]:
    """Bind the chokepoint-minted run id and return a reset token.

    The HTTP path's ``request_context_middleware`` brackets this
    contextvar with ``None`` at the start of every request so a value
    written by one handler never leaks into the next request.
    """
    return _request_run_id.set(run_id)


def current_recorder_degraded() -> bool:
    """Return whether the submit chokepoint hit a persistence failure.

    The submit chokepoint in ``runtime/submit_recorder`` writes its
    runs / tasks rows best-effort: a ``sqlite3.Error`` / ``OSError``
    during the registry write must not break an already-successful
    remote submission, but it leaves the run un-tracked locally
    (``current_run_id() is None``, ``RunRegistry.get_run`` returns
    ``None``) so a client polling ``GET /v1/runs/{id}`` would see a
    permanent ``404``. Setting this flag lets the HTTP layer signal
    the degraded-tracking case explicitly, distinguishing it from
    the analyst dedup-hit passthrough that also returns
    ``id=None`` / ``task_ids=[]`` but for a legitimate reason.
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
        token: A token returned by ``bind_request_user``,
            ``bind_request_id``, ``bind_run_id``, or
            ``bind_recorder_degraded``.
    """
    token.var.reset(token)


@contextmanager
def request_context(
    user_id: Optional[str],
    request_id: Optional[str],
    run_id: Optional[str] = None,
) -> Generator[None, None, None]:
    """Bind user, request, and run ids for the duration of the block.

    Args:
        user_id: Authenticated user id, or None.
        request_id: Correlation id, or None.
        run_id: Submit chokepoint's run id, or None. Defaults to
            ``None`` since most callers (test harnesses, the HTTP
            middleware entry) want the run id to be discovered later
            by the chokepoint inside the block.

    Yields:
        None while all four contextvars are bound. The
        recorder-degraded flag is always seeded to ``False`` at entry
        so a chokepoint failure can only flag the current request,
        never inherit a stale ``True`` from an earlier one.
    """
    user_token = bind_request_user(user_id)
    id_token = bind_request_id(request_id)
    run_token = bind_run_id(run_id)
    degraded_token = bind_recorder_degraded(False)
    try:
        yield
    finally:
        reset_request_var(degraded_token)
        reset_request_var(run_token)
        reset_request_var(id_token)
        reset_request_var(user_token)
