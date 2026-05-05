# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the func_cache decorator."""

import pytest

from mcp_server_phytomni.func_cache.decorator import func_cache

pytestmark = pytest.mark.unit


def test_func_cache_reuses_result_and_exposes_info(tmp_path):
    calls = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "decorator.sqlite"),
        key_params=["value"],
        ttl=60,
    )
    def double(value, noise=None):
        calls["count"] += 1
        return {"value": value * 2, "call": calls["count"]}

    first = double(2, noise="leaf")
    second = double(2, noise="root")

    assert first == second == {"value": 4, "call": 1}
    assert calls["count"] == 1
    assert double.cache_info() == {"hits": 1, "misses": 1, "count": 1}

    double.cache_clear()

    assert double.cache_info()["count"] == 0
    assert double(2)["call"] == 2


def test_func_cache_respects_zero_ttl(tmp_path):
    calls = {"count": 0}

    @func_cache(db_path=str(tmp_path / "ttl.sqlite"), ttl=0)
    def next_value():
        calls["count"] += 1
        return calls["count"]

    assert next_value() == 1
    assert next_value() == 2


def test_func_cache_does_not_cache_exceptions(tmp_path):
    calls = {"count": 0}

    @func_cache(db_path=str(tmp_path / "exceptions.sqlite"))
    def flaky_value():
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("boom")
        return "ok"

    with pytest.raises(ValueError, match="boom"):
        flaky_value()

    assert flaky_value() == "ok"
    assert calls["count"] == 2
