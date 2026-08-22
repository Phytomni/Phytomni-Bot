# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for result-delivery child output layout helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.result_run_layout import (
    is_legacy_shared_output_dir,
    is_unallocated_default_output_dir,
    result_child_output_dir,
    result_run_root_from_child,
    reusable_caller_output_dir,
)


def test_result_child_output_dir_is_stable_and_one_based() -> None:
    """Child directories use stable one-based, zero-padded ordinals."""
    assert result_child_output_dir("obs://bucket/owners/alice/run-1", 0) == (
        "obs://bucket/owners/alice/run-1/children/part-001"
    )
    assert result_child_output_dir("obs://bucket/owners/alice/run-1/", 1) == (
        "obs://bucket/owners/alice/run-1/children/part-002"
    )


def test_result_run_root_from_child_requires_the_scoped_layout() -> None:
    """Only exact bounded child paths project back to their run root."""
    assert result_run_root_from_child(
        "obs://bucket/run/children/part-001"
    ) == ("obs://bucket/run")
    assert result_run_root_from_child(
        "obs://bucket/run/children/part-199"
    ) == ("obs://bucket/run")
    for invalid_child in ("part-1", "part-000", "part-200", "part-999"):
        with pytest.raises(ValueError, match="result child"):
            result_run_root_from_child(
                f"obs://bucket/run/children/{invalid_child}"
            )


def test_result_child_output_dir_rejects_the_upper_bound() -> None:
    """The helper rejects indexes beyond the bounded child range."""
    assert result_child_output_dir("obs://bucket/run", 198).endswith(
        "part-199"
    )
    with pytest.raises(ValueError, match="invalid result child layout"):
        result_child_output_dir("obs://bucket/run", 199)


def test_unallocated_default_covers_the_shared_dump_and_children() -> None:
    """The config placeholder and its descendants are not run-scoped."""
    default = "/obs/phytomni/agent_data/test/output"
    assert is_unallocated_default_output_dir(default, default) is True
    assert is_unallocated_default_output_dir(f"{default}/", default) is True
    assert (
        is_unallocated_default_output_dir(
            f"{default}/children/part-001",
            default,
        )
        is True
    )
    assert (
        is_unallocated_default_output_dir(
            "/obs/phytomni/agent_data/users/alice/run-1",
            default,
        )
        is False
    )
    assert is_unallocated_default_output_dir("", default) is False
    assert is_unallocated_default_output_dir(default, "") is False


def test_legacy_shared_output_dir_rejects_old_fingerprint_dump() -> None:
    """The pre-jobs shared dump is not a reusable isolated job root."""
    fingerprint = "b" * 64
    dump = f"/obs/phytomni/agent_data/shared/{fingerprint}/output"
    assert is_legacy_shared_output_dir(dump) is True
    assert is_legacy_shared_output_dir(f"{dump}/children/part-001") is True
    assert (
        is_legacy_shared_output_dir(
            f"/obs/phytomni/agent_data/shared/{fingerprint}/jobs/"
            "run-1/output/children/part-001"
        )
        is False
    )
    assert is_legacy_shared_output_dir(
        "/obs/phytomni/agent_data/user_data/x"
    ) is (False)


def test_reusable_caller_output_dir_rejects_shared_dumps() -> None:
    """Config dump and legacy fingerprint dump are not caller-owned roots."""
    default = "/obs/phytomni/agent_data/test/output"
    owned = "/obs/phytomni/agent_data/users/alice/run-1"
    fingerprint = "b" * 64
    dump = f"/obs/phytomni/agent_data/shared/{fingerprint}/output"
    isolated = (
        f"/obs/phytomni/agent_data/shared/{fingerprint}/jobs/run-1/output"
    )

    assert reusable_caller_output_dir(default, default) == ""
    assert (
        reusable_caller_output_dir(f"{default}/children/part-001", default)
        == ""
    )
    assert reusable_caller_output_dir("", default) == ""
    assert reusable_caller_output_dir(owned, default) == owned
    assert reusable_caller_output_dir(
        f"{owned}/children/part-001", default
    ) == f"{owned}/children/part-001"
    assert reusable_caller_output_dir(dump, default) == ""
    assert reusable_caller_output_dir(isolated, default) == isolated
