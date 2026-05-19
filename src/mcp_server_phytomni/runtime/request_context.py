# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Per-request user and request-id context for the HTTP API.

Functions: current_request_user, current_request_id, bind_request_user,
    bind_request_id, reset_request_var, request_context.

The MCP stdio path never binds these, so getters return None and the
agent layer keeps its existing anonymous behavior unchanged.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Optional

__all__ = [
    "current_request_user",
    "current_request_id",
    "bind_request_user",
    "bind_request_id",
    "reset_request_var",
    "request_context",
]

_request_user: ContextVar[Optional[str]] = ContextVar(
    "phytomni_request_user", default=None
)
_request_id: ContextVar[Optional[str]] = ContextVar(
    "phytomni_request_id", default=None
)


def current_request_user() -> Optional[str]:
    """Return the authenticated user id bound to this request, if any."""
    return _request_user.get()


def current_request_id() -> Optional[str]:
    """Return the correlation id bound to this request, if any."""
    return _request_id.get()


def bind_request_user(user_id: Optional[str]) -> Token[Optional[str]]:
    """Bind the request user id and return a reset token."""
    return _request_user.set(user_id)


def bind_request_id(request_id: Optional[str]) -> Token[Optional[str]]:
    """Bind the request correlation id and return a reset token."""
    return _request_id.set(request_id)


def reset_request_var(token: Token[Optional[str]]) -> None:
    """Reset a request contextvar to its prior value.

    Args:
        token: A token returned by ``bind_request_user`` or
            ``bind_request_id``.
    """
    token.var.reset(token)


@contextmanager
def request_context(
    user_id: Optional[str], request_id: Optional[str]
) -> Generator[None, None, None]:
    """Bind user and request ids for the duration of the block.

    Args:
        user_id: Authenticated user id, or None.
        request_id: Correlation id, or None.

    Yields:
        None while both contextvars are bound.
    """
    user_token = bind_request_user(user_id)
    id_token = bind_request_id(request_id)
    try:
        yield
    finally:
        reset_request_var(id_token)
        reset_request_var(user_token)
