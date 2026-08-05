# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Tests for result-delivery child output layout helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.result_run_layout import (
    result_child_output_dir,
    result_run_root_from_child,
)


def test_result_child_output_dir_is_stable_and_one_based() -> None:
    assert result_child_output_dir("obs://bucket/owners/alice/run-1", 0) == (
        "obs://bucket/owners/alice/run-1/children/part-001"
    )
    assert result_child_output_dir("obs://bucket/owners/alice/run-1/", 1) == (
        "obs://bucket/owners/alice/run-1/children/part-002"
    )


def test_result_run_root_from_child_requires_the_scoped_layout() -> None:
    assert result_run_root_from_child("obs://bucket/run/children/part-001") == (
        "obs://bucket/run"
    )
    with pytest.raises(ValueError, match="result child"):
        result_run_root_from_child("obs://bucket/run/children/part-1")
