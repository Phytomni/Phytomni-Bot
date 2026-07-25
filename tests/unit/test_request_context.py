# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the per-request contextvar helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.locale import current_effective_locale
from mcp_server_phytomni.runtime.request_context import (
    bind_accepted_task_ids,
    bind_request_id,
    bind_request_user,
    bind_run_id,
    current_accepted_task_ids,
    current_request_id,
    current_request_user,
    current_run_id,
    request_context,
    reset_request_var,
)

pytestmark = pytest.mark.unit


def test_unbound_getters_return_none() -> None:
    """Outside any bound block identity getters yield their defaults."""
    assert current_request_user() is None
    assert current_request_id() is None
    assert current_run_id() is None
    assert current_accepted_task_ids() == ()
    assert current_effective_locale() == "en-US"


def test_run_id_bind_get_reset() -> None:
    """``bind_run_id`` flips the getter and ``reset`` restores it."""
    token = bind_run_id("run-X")
    try:
        assert current_run_id() == "run-X"
    finally:
        reset_request_var(token)
    assert current_run_id() is None


def test_three_contextvars_are_independent() -> None:
    """Binding one var must not affect the other two."""
    user_token = bind_request_user("alice")
    request_token = bind_request_id("req-1")
    try:
        assert current_request_user() == "alice"
        assert current_request_id() == "req-1"
        assert current_run_id() is None  # not bound here
    finally:
        reset_request_var(request_token)
        reset_request_var(user_token)


def test_request_context_brackets_all_three() -> None:
    """``request_context`` enters with the trio bound and exits clean."""
    with request_context("alice", "req-1", "run-A"):
        assert current_request_user() == "alice"
        assert current_request_id() == "req-1"
        assert current_run_id() == "run-A"
    assert current_request_user() is None
    assert current_request_id() is None
    assert current_run_id() is None


def test_request_context_defaults_run_id_to_none() -> None:
    """Omitting ``run_id`` binds None so the chokepoint can fill it later."""
    with request_context("alice", "req-1"):
        assert current_run_id() is None
        # A nested rebind inside the block models the chokepoint write:
        chokepoint_token = bind_run_id("run-late")
        try:
            assert current_run_id() == "run-late"
        finally:
            reset_request_var(chokepoint_token)
        assert current_run_id() is None
    assert current_run_id() is None


def test_request_context_brackets_effective_locale() -> None:
    """The request scope restores locale after a bound request exits."""
    with request_context("alice", "req-locale", locale="zh-CN"):
        assert current_effective_locale() == "zh-CN"
    assert current_effective_locale() == "en-US"


def test_request_context_seeds_and_restores_accepted_task_ids() -> None:
    """Accepted task ids are request-local and restored after the scope."""
    bind_accepted_task_ids(("outer-task",))

    with request_context("alice", "req-accepted"):
        assert current_accepted_task_ids() == ()
        bind_accepted_task_ids(("accepted-1", "accepted-1", "", "accepted-2"))
        assert current_accepted_task_ids() == ("accepted-1", "accepted-2")

    assert current_accepted_task_ids() == ("outer-task",)
