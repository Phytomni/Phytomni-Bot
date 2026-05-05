# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for shared agent registry helpers."""

from pydantic import BaseModel, SecretStr

from mcp_server_phytomni.agent_registry import agent_fingerprint_values
from mcp_server_phytomni.agent_registry import clear_agent_registry
from mcp_server_phytomni.agent_registry import get_cached_agent


class DemoConfig(BaseModel):
    """Small config model used to test fingerprint dumping."""

    MODEL: str
    COUNT: int = 1


class DemoSensitiveConfig(BaseModel):
    """Small sensitive config model used to test secret omission."""

    API_KEY: SecretStr
    BASE_URL: str


def test_agent_registry_reuses_agent_for_matching_fingerprint():
    clear_agent_registry()
    created = 0

    def factory() -> object:
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
    fingerprint = agent_fingerprint_values(
        config=DemoConfig(MODEL="alpha"),
        nested={"items": [DemoConfig(MODEL="beta", COUNT=2)]},
    )

    assert fingerprint == {
        "config": {"MODEL": "alpha", "COUNT": 1},
        "nested": {"items": [{"MODEL": "beta", "COUNT": 2}]},
    }


def test_agent_registry_omits_secret_fields_from_cache_key():
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
