# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for shared runtime ID and path policy helpers.

Covers deterministic run identities, shared task path keys, default user id
resolution, path-segment rejection, and readable slug generation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mcp_server_phytomni.storage.path_policy import (
    AGENT_DATA_ROOT,
    DEFAULT_USER_ID,
    IdFactory,
    PathPolicyError,
    RunIdentity,
    resolve_user_id,
    safe_path_segment,
    shared_job_output_key,
    shared_output_key,
    task_downloads_key,
    task_output_key,
    task_tmp_key,
)

pytestmark = pytest.mark.unit


def _fixed_time() -> datetime:
    """Return a fixed UTC timestamp for deterministic tests."""
    return datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC)


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


def test_task_downloads_key_uses_shared_run_root_and_safe_segment():
    """Verify task_downloads_key shares the run root and slugs unsafe names."""
    factory = IdFactory(now=_fixed_time, token_factory=lambda _: "abcdef01")
    identity = RunIdentity.create("researcher", "workflow", factory)

    assert task_downloads_key(identity, "rna seq") == (
        "agent_data/user_data/researcher/runs/20260507/"
        "20260507T010203Z-workflow-researcher-abcdef01/"
        "rna-seq/downloads/"
    )
    assert task_downloads_key(identity, "ChIP-Seq Run 01") == (
        "agent_data/user_data/researcher/runs/20260507/"
        "20260507T010203Z-workflow-researcher-abcdef01/"
        "ChIP-Seq-Run-01/downloads/"
    )


def test_shared_output_key_is_tenant_neutral():
    """Verify the shared output key omits user and run namespaces."""
    key = shared_output_key("a" * 64)
    assert key == f"{AGENT_DATA_ROOT}/shared/{'a' * 64}/output/"
    assert "user_data" not in key
    assert "/runs/" not in key


def test_shared_job_output_key_isolates_one_job():
    """Each public-data EI job gets its own directory under the digest."""
    fingerprint = "a" * 64
    key = shared_job_output_key(fingerprint, "20260818T010203Z-run-analyst")
    assert key == (
        f"{AGENT_DATA_ROOT}/shared/{fingerprint}/jobs/"
        "20260818T010203Z-run-analyst/output/"
    )
    assert key.startswith(f"{AGENT_DATA_ROOT}/shared/{fingerprint}/jobs/")
    assert "/user_data/" not in key


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


def test_resolve_user_id_pins_to_relay_namespace(
    monkeypatch: pytest.MonkeyPatch,
):
    """In relay mode the configured tenant id overrides the passed user."""
    monkeypatch.setenv("RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_USER_ID", "cust42")

    assert resolve_user_id("enduser7") == "cust42"
    ident = RunIdentity.create(user_id="enduser7", scope="analysis")
    assert ident.user_id == "cust42"
    assert task_output_key(ident, "t").startswith(
        "agent_data/user_data/cust42/"
    )


def test_resolve_user_id_ignores_relay_id_in_normal_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    """Without relay mode the passed user id is used unchanged."""
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.setenv("PHYTOMNI_RELAY_USER_ID", "cust42")

    assert resolve_user_id("enduser7") == "enduser7"


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
    """Verify path-like values cannot become path segments.

    Args:
        value: Unsafe path-like value expected to fail.
    """
    with pytest.raises(PathPolicyError):
        safe_path_segment(value, "fallback")


def test_safe_path_segment_slugs_readable_values():
    """Verify readable labels become safe path segments."""
    assert safe_path_segment("RNA seq task", "task") == "RNA-seq-task"
