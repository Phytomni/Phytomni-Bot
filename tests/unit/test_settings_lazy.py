# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the lazy/cached SensitiveConfig getter.

``get_sensitive_config`` is an ``@lru_cache``-backed factory that the 17+
``SensitiveConfig.load()`` call sites across the codebase now share so
the decrypt + pydantic-settings load runs once per process. These tests
pin the cache identity, the load delegation, and the manual reset hook
that the autouse fixture in ``tests/conftest.py`` relies on.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.config.settings import (
    SensitiveConfig,
    get_sensitive_config,
)

pytestmark = pytest.mark.unit


def test_get_sensitive_config_returns_cached_instance() -> None:
    """Repeat calls return the same instance until the cache is cleared."""
    first = get_sensitive_config()
    second = get_sensitive_config()

    assert first is second


def test_get_sensitive_config_returns_sensitive_config_type() -> None:
    """The cached value is an actual ``SensitiveConfig`` instance."""
    assert isinstance(get_sensitive_config(), SensitiveConfig)


def test_sensitive_config_load_delegates_to_cached_getter() -> None:
    """``SensitiveConfig.load()`` is now a thin delegator to the cache.

    A change to either side that decoupled them would surface as a
    failing identity assertion here.
    """
    cached = get_sensitive_config()
    via_load = SensitiveConfig.load()

    assert cached is via_load


def test_cache_clear_releases_the_instance() -> None:
    """``cache_clear`` makes the next call return a fresh instance.

    The autouse fixture in conftest.py drops the cache between every
    test; this case proves the contract that fixture relies on.
    """
    first = get_sensitive_config()
    get_sensitive_config.cache_clear()
    second = get_sensitive_config()

    assert first is not second
    assert isinstance(second, SensitiveConfig)


def test_module_level_loads_share_cache_after_warm_up() -> None:
    """``SENSITIVE_CONFIG = SensitiveConfig.load()`` shares the cache.

    Walking the production import chain (``api.app``,
    ``agents.chat.service``) triggers ``SensitiveConfig.load()`` at
    module-import time on several paths. Each of those calls flows
    through the same cached helper, so after the first call every
    subsequent ``load()`` returns the cached instance.
    """
    warmed = SensitiveConfig.load()
    again = SensitiveConfig.load()
    direct = get_sensitive_config()

    assert warmed is again
    assert warmed is direct
