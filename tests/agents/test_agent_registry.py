# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for shared agent registry helpers.

Covers cache reuse, Pydantic fingerprint dumping, and secret-field omission in
agent registry cache keys.
"""

import pytest
from pydantic import BaseModel, SecretStr

from mcp_server_phytomni.runtime.agent_registry import (
    agent_fingerprint_values,
    clear_agent_registry,
    get_cached_agent,
)

pytestmark = pytest.mark.agent


class DemoConfig(BaseModel):
    """Small config model used to test fingerprint dumping.

    Attributes:
        MODEL: Model identifier included in fingerprints.
        COUNT: Integer option included in fingerprints.
    """

    MODEL: str
    COUNT: int = 1


class DemoSensitiveConfig(BaseModel):
    """Small sensitive config model used to test secret omission.

    Attributes:
        API_KEY: Secret field omitted from fingerprints.
        BASE_URL: Non-secret field retained in fingerprints.
    """

    API_KEY: SecretStr
    BASE_URL: str


def test_agent_registry_reuses_agent_for_matching_fingerprint():
    """Verify agent registry reuses agent for matching fingerprint.

    Returns:
        None after cache identity assertions pass.
    """
    clear_agent_registry()
    created = 0

    def factory() -> object:
        """Create a distinct object and count factory calls.

        Returns:
            New object instance for the registry cache.
        """
        nonlocal created
        created += 1
        return object()

    try:
        first = get_cached_agent("demo", factory, {"model": "alpha"})
        second = get_cached_agent("demo", factory, {"model": "alpha"})
        third = get_cached_agent("demo", factory, {"model": "beta"})
    finally:
        clear_agent_registry()

    assert first is second
    assert first is not third
    assert created == 2


def test_agent_fingerprint_values_dump_pydantic_models():
    """Verify agent fingerprint values dump pydantic models."""
    fingerprint = agent_fingerprint_values(
        config=DemoConfig(MODEL="alpha"),
        nested={"items": [DemoConfig(MODEL="beta", COUNT=2)]},
    )

    assert fingerprint == {
        "config": {"MODEL": "alpha", "COUNT": 1},
        "nested": {"items": [{"MODEL": "beta", "COUNT": 2}]},
    }


def test_agent_registry_omits_secret_fields_from_cache_key():
    """Verify agent registry omits secret fields from cache key."""
    clear_agent_registry()

    try:
        first = get_cached_agent(
            "secret-demo",
            object,
            agent_fingerprint_values(
                sensitive_config=DemoSensitiveConfig(
                    API_KEY=SecretStr("one"),
                    BASE_URL="https://example.invalid",
                )
            ),
        )
        second = get_cached_agent(
            "secret-demo",
            object,
            agent_fingerprint_values(
                sensitive_config=DemoSensitiveConfig(
                    API_KEY=SecretStr("two"),
                    BASE_URL="https://example.invalid",
                )
            ),
        )
    finally:
        clear_agent_registry()

    assert first is second
