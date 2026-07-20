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

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.agents.analyst.submission import (
    SubmissionOptions,
    _submit_user_and_thread_id,
    resolve_submission_options,
)

pytestmark = pytest.mark.unit


def _config() -> SimpleNamespace:
    """Return a small config object for pure option-resolution tests."""
    return SimpleNamespace(
        USER_ID="config-user",
        CREATE_DIR=True,
        OUTPUT_DIR="/obs/default",
        COMPUTE_RESOURCE="medium",
    )


def test_resolve_submission_options_uses_config_defaults() -> None:
    """Omitted overrides resolve from the supplied config object."""
    options = resolve_submission_options(_config(), {})

    assert options.user_id == "config-user"
    assert options.is_create_dir is True
    assert options.output_dir == "/obs/default"
    assert options.compute_resource == "medium"


def test_resolve_submission_options_preserves_falsy_caller_values() -> None:
    """Explicit false and blank values are not replaced by defaults."""
    options = resolve_submission_options(
        _config(),
        {
            "is_create_dir": False,
            "output_dir": "",
            "compute_resource": "large",
            "user_id": "caller-user",
            "unknown_option": "ignored",
        },
    )

    assert options.is_create_dir is False
    assert options.output_dir == ""
    assert options.compute_resource == "large"
    assert options.user_id == "caller-user"
    assert not hasattr(options, "unknown_option")


def test_resolve_submission_options_treats_none_as_omitted() -> None:
    """``None`` keeps the config default while false values remain explicit."""
    options = resolve_submission_options(
        _config(),
        {
            "is_create_dir": None,
            "output_dir": None,
            "compute_resource": None,
            "user_id": None,
        },
    )

    assert options.user_id == "config-user"
    assert options.is_create_dir is True
    assert options.output_dir == "/obs/default"
    assert options.compute_resource == "medium"


def test_submission_options_build_shared_arun_kwargs() -> None:
    """The shared arun projection retains caller goal and data values."""
    options = resolve_submission_options(_config(), {"output_dir": ""})

    assert SubmissionOptions.build_arun_kwargs(
        "goal",
        output_dir=options.output_dir,
        compute_resource=options.compute_resource,
        data_list={"input.tsv": "counts"},
    ) == {
        "query": "goal",
        "goal_description": "goal",
        "output_dir": "",
        "compute_resource": "medium",
        "preset_data_list": {"input.tsv": "counts"},
    }


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
