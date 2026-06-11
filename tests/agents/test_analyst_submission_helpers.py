# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst/submission.py pure helpers.

Pins ``_submit_user_and_thread_id`` (RunIdentity-fallback branch). The
fingerprint / reuse-status helpers moved to ``runtime/task_dedup.py``;
their invariants now live in ``tests/unit/test_task_dedup.py``.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.analyst.submission import (
    _submit_user_and_thread_id,
)

pytestmark = pytest.mark.unit


def test_submit_user_and_thread_id_uses_user_id_when_present() -> None:
    """A populated user id is returned verbatim for both user and thread."""
    user_id, thread_id = _submit_user_and_thread_id(
        "alice", scope="analyst-submit", operation="submit"
    )

    assert user_id == "alice"
    assert thread_id == "alice"


def test_submit_user_and_thread_id_coerces_non_string_user_id() -> None:
    """Non-string user ids (e.g. ints) are coerced to str for both fields."""
    user_id, thread_id = _submit_user_and_thread_id(
        42, scope="analyst-submit", operation="submit"
    )

    assert user_id == "42"
    assert thread_id == "42"


def test_submit_user_and_thread_id_falls_back_to_run_identity() -> None:
    """Missing user id resolves through ``RunIdentity`` for both fields."""
    user_id, thread_id = _submit_user_and_thread_id(
        None, scope="analyst-submit", operation="submit"
    )

    assert isinstance(user_id, str)
    assert isinstance(thread_id, str)
    assert user_id  # anonymous fallback is non-empty
    assert thread_id  # scoped fallback is non-empty
    assert thread_id != user_id  # scoped id encodes operation, not just user
