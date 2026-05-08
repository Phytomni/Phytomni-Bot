# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for shared runtime ID and path policy helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mcp_server_phytomni.storage.path_policy import (
    DEFAULT_USER_ID,
    IdFactory,
    PathPolicyError,
    RunIdentity,
    resolve_user_id,
    safe_path_segment,
    task_output_key,
    task_tmp_key,
)

pytestmark = pytest.mark.unit


def _fixed_time() -> datetime:
    """Return a fixed UTC timestamp for deterministic tests."""
    return datetime(2026, 5, 7, 1, 2, 3, tzinfo=timezone.utc)


def test_run_identity_uses_stable_timestamp_and_token():
    """Verify deterministic run identity construction."""
    factory = IdFactory(now=_fixed_time, token_factory=lambda _: "abc12345")

    identity = RunIdentity.create(
        user_id="user-a",
        scope="analysis task",
        id_factory=factory,
    )

    assert identity.user_id == "user-a"
    assert identity.date_stamp == "20260507"
    assert identity.run_id == "20260507T010203Z-analysis-task-user-a-abc12345"


def test_task_keys_are_derived_from_one_run_identity():
    """Verify task OBS keys share the same run root."""
    factory = IdFactory(now=_fixed_time, token_factory=lambda _: "abcdef01")
    identity = RunIdentity.create("researcher", "workflow", factory)

    assert task_output_key(identity, "rna seq") == (
        "agent_data/user_data/researcher/runs/20260507/"
        "20260507T010203Z-workflow-researcher-abcdef01/"
        "rna-seq/output/"
    )
    assert task_tmp_key(identity, "rna seq", "submit.json") == (
        "agent_data/user_data/researcher/runs/20260507/"
        "20260507T010203Z-workflow-researcher-abcdef01/"
        "rna-seq/tmp/submit.json"
    )


def test_id_factory_generates_unique_ids():
    """Verify generated IDs are unique for repeated calls."""
    factory = IdFactory(now=_fixed_time)

    assert factory.run_id("analysis", "user-a") != factory.run_id(
        "analysis",
        "user-a",
    )


def test_resolve_user_id_defaults_to_anonymous():
    """Verify missing users use a stable non-UUID fallback."""
    assert resolve_user_id(None) == DEFAULT_USER_ID
    assert resolve_user_id("") == DEFAULT_USER_ID


@pytest.mark.parametrize(
    "value",
    [
        ".",
        "..",
        "../secret",
        "folder/file",
        "folder\\file",
    ],
)
def test_safe_path_segment_rejects_path_values(value: str):
    """Verify path-like values cannot become path segments."""
    with pytest.raises(PathPolicyError):
        safe_path_segment(value, "fallback")


def test_safe_path_segment_slugs_readable_values():
    """Verify readable labels become safe path segments."""
    assert safe_path_segment("RNA seq task", "task") == "RNA-seq-task"
