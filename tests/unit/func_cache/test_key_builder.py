# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for deterministic cache key construction.

Covers stable key generation, selected key params, excluded infrastructure
arguments, config errors, and serialization failures.
"""

import pytest

from mcp_server_phytomni.func_cache.exceptions import (
    CacheConfigError,
    SerializationError,
)
from mcp_server_phytomni.func_cache.key_builder import KeyBuilder

pytestmark = pytest.mark.unit


def sample_function(alpha, beta=2, *, gamma=None):
    """Return arguments for cache-key construction tests.

    Args:
        alpha: Required positional-or-keyword argument.
        beta: Optional positional-or-keyword argument.
        gamma: Optional keyword-only argument.

    Returns:
        Tuple of received argument values.
    """
    return alpha, beta, gamma


def test_build_key_is_stable_across_call_styles():
    """Verify build key is stable across call styles."""
    key_builder = KeyBuilder(sample_function)

    positional_key = key_builder.build_key((1,), {"gamma": "leaf"})
    keyword_key = key_builder.build_key((), {"alpha": 1, "gamma": "leaf"})

    assert positional_key == keyword_key


def test_key_params_ignore_unselected_arguments():
    """Verify key params ignore unselected arguments."""
    key_builder = KeyBuilder(sample_function, key_params=["alpha"])

    first_key = key_builder.build_key((1,), {"beta": 2, "gamma": "leaf"})
    second_key = key_builder.build_key((1,), {"beta": 99, "gamma": "root"})

    assert first_key == second_key
    assert key_builder.selected_params() == ("alpha",)


def test_exclude_params_omit_infrastructure_arguments():
    """Verify exclude params omit infrastructure arguments."""
    key_builder = KeyBuilder(
        sample_function,
        exclude_params=["gamma"],
    )

    first_key = key_builder.build_key((1,), {"gamma": "client-a"})
    second_key = key_builder.build_key((1,), {"gamma": "client-b"})

    assert key_builder.key_params == ["alpha", "beta"]
    assert first_key == second_key


def test_key_params_and_exclude_params_cannot_overlap():
    """Verify key params and exclude params cannot overlap."""
    with pytest.raises(CacheConfigError, match="both included and excluded"):
        KeyBuilder(
            sample_function,
            key_params=["alpha", "gamma"],
            exclude_params=["gamma"],
        )


def test_invalid_key_param_raises_config_error():
    """Verify invalid key param raises config error."""
    with pytest.raises(CacheConfigError, match="does not exist"):
        KeyBuilder(sample_function, key_params=["missing"])


def test_unserializable_argument_raises_serialization_error():
    """Verify unserializable argument raises serialization error."""
    key_builder = KeyBuilder(sample_function)

    with pytest.raises(SerializationError, match="Failed to build cache key"):
        key_builder.build_key((lambda value: value,), {})
