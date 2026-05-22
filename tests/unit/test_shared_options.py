# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for agents.shared.options retry-policy fallback.

Pins the retriable_codes resolution path: an explicit kwarg wins, but
when the caller omits it the helper falls back to the config-defined
list. The fallback path was previously uncovered and silent regressions
there would break the wrapper retry policy without surfacing a test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.agents.shared.options import retry_codes_from_kwargs

pytestmark = pytest.mark.unit


def test_retry_codes_uses_config_default_when_kwarg_missing() -> None:
    """Omitting ``retriable_codes`` returns the config-defined list."""
    config = SimpleNamespace(RETRIABLE_CODES=[502, 503])

    assert retry_codes_from_kwargs({}, config) == [502, 503]


def test_retry_codes_prefers_explicit_kwarg() -> None:
    """An explicit ``retriable_codes`` kwarg wins over the config default."""
    config = SimpleNamespace(RETRIABLE_CODES=[502])

    assert retry_codes_from_kwargs(
        {"retriable_codes": [429, 500]}, config
    ) == [429, 500]
