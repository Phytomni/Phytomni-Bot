# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Independent configuration-shaped fakes used by agent tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from mcp_server_phytomni.config.defaults import ServerConfig

__all__ = [
    "fake_chat_config",
    "fake_sensitive_config",
    "fake_scratch_config",
]


def fake_scratch_config(tmp_path: Path) -> ServerConfig:
    """Build the minimal config surface used by ``scratch_server_dir``."""
    return cast(
        ServerConfig,
        SimpleNamespace(
            BUCKET_NAME="phytomni",
            TEMP_DIR=str(tmp_path / "fallback"),
        ),
    )


def fake_chat_config(**overrides: object) -> SimpleNamespace:
    """Build the complete config surface consumed by chat adapters.

    Each call creates fresh list and mapping values so one test cannot mutate
    another test's fixture. ``overrides`` is intentionally data-only: callers
    may pin a domain-specific value without adding behavior to this helper.
    """
    values: dict[str, object] = {
        "PROMPT_FILE": "prompt.yaml",
        "PROMPT_PATH": "/tmp/prompts",
        "FREQUENCY_PENALTY": 0.0,
        "N": 1,
        "PRESENCE_PENALTY": 0.0,
        "REASONING_EFFORT": "medium",
        "RESPONSE_FORMAT": {"type": "text"},
        "STREAM": False,
        "TEMPERATURE": 0.2,
        "TOP_P": 0.9,
        "USER": "consumer-user",
        "TIMEOUT": 120.0,
        "RETRIABLE_CODES": [429, 500, 502, 503, 504],
        "MAX_RETRIES": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_sensitive_config() -> SimpleNamespace:
    """Build the sensitive config fields consumed by chat adapters."""

    def get_secret_value() -> str:
        """Return the deterministic test-only API key."""
        return "sk-test"

    return SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=get_secret_value),
        BASE_URL="https://llm.example/v1",
        MODEL_ID="phyto-llm-v1",
    )
