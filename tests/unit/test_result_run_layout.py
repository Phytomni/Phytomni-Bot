# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for result-delivery child output layout helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.result_run_layout import (
    result_child_output_dir,
    result_run_root_from_child,
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
