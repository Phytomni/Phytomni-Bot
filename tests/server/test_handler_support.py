# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the MCP handler-support assembly helpers.

The helpers feed each MCP handler's wrapper call with config-sourced
kwargs (chat, retrieve, OBS, retry, coder, analysis platform). These
tests pin the dict shape and key names so a config rename or a
forgotten field cannot silently drift the wrapper contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.mcp.handler_support import (
    HandlerRuntime,
    analysis_platform_kwargs,
    chat_kwargs,
    coder_kwargs,
    load_handler_runtime,
    obs_kwargs,
    retrieve_kwargs,
    retry_kwargs,
)

pytestmark = pytest.mark.server


class _FakeSecret:
    """Stand-in for a Pydantic SecretStr value used in tests."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _fake_sensitive() -> SensitiveConfig:
    """Build a SensitiveConfig-shaped fake with the fields helpers read.

    Returns the SimpleNamespace cast to SensitiveConfig so mypy accepts
    it at helper call sites; the helpers only ever read the listed
    attributes, never invoke pydantic validation, so the structural
    duck-typed object passes through every code path under test.
    """
    namespace = SimpleNamespace(
        API_KEY=_FakeSecret("fake-api-key"),
        BASE_URL="https://example.invalid/llm",
        MODEL_ID="fake-model",
        CODER_URL="https://example.invalid/coder",
        CODER_MODEL="fake-coder-model",
        CODER_API_KEY=_FakeSecret("fake-coder-api-key"),
    )
    return cast(SensitiveConfig, namespace)


def _fake_config() -> SimpleNamespace:
    """Build a chat/retrieve/OBS/retry/analysis config fake.

    Every attribute name mirrors a UPPERCASE config field name read by
    one of the helpers, so a missing attribute would surface as a
    direct AttributeError rather than a silent kwarg gap.
    """
    return SimpleNamespace(
        PROMPT_FILE="prompts.yaml",
        PROMPT_PATH="agent/path",
        FREQUENCY_PENALTY=0.0,
        N=1,
        PRESENCE_PENALTY=0.0,
        REASONING_EFFORT="low",
        RESPONSE_FORMAT={"type": "text"},
        STREAM=False,
        TEMPERATURE=0.2,
        TOP_P=1.0,
        USER="agent-user",
        MAX_TOKENS=2048,
        RETRIEVE_URL="https://example.invalid/retrieve",
        REPO_ID_DICT={"r1": "repo-one"},
        PAGE_NUM=1,
        FILTER_STRING="filter",
        SCOPE="scope",
        EXTRA_REPO_IDS=["extra-1"],
        RERANK_URL="https://example.invalid/rerank",
        RERANK_BATCH_SIZE=8,
        SCORE_THRESHOLD=0.3,
        TOP_N=5,
        OBS_SERVER="obs.example.invalid",
        BUCKET_NAME="bucket",
        PART_SIZE=4096,
        TASK_NUM=2,
        MAX_CONCURRENCY=4,
        MAX_WORKERS=4,
        TIMEOUT=30,
        RETRIABLE_CODES=[502, 503],
        MAX_RETRIES=3,
        ANALYSIS_URL="https://example.invalid/analysis",
        ANALYSIS_REGION="region-a",
        RESOURCE={"small": {}},
        APP_ID={"app": "id"},
    )


def test_load_handler_runtime_returns_credentials() -> None:
    """``load_handler_runtime`` exposes sensitive config + OBS credentials.

    Drives the real ``SensitiveConfig.load()`` so this also catches a
    regression in the testing-mode env shim used by other handler tests.
    """
    runtime = load_handler_runtime()

    assert isinstance(runtime, HandlerRuntime)
    access_key, secret = runtime.obs_credentials
    assert isinstance(access_key, str)
    assert isinstance(secret, str)
    assert access_key != ""
    assert secret != ""


def test_chat_kwargs_contains_chat_and_retry_blocks() -> None:
    """``chat_kwargs`` returns chat fields + response_format + max_tokens."""
    result = chat_kwargs(_fake_config(), _fake_sensitive())

    assert result["api_key"] == "fake-api-key"
    assert result["base_url"] == "https://example.invalid/llm"
    assert result["model"] == "fake-model"
    assert result["prompt_file"] == "prompts.yaml"
    assert result["prompt_path"] == "agent/path"
    assert result["response_format"] == {"type": "text"}
    assert result["max_tokens"] == 2048
    assert result["timeout"] == 30
    assert result["retriable_codes"] == [502, 503]
    assert result["max_retries"] == 3


def test_retrieve_kwargs_has_ten_documented_keys() -> None:
    """``retrieve_kwargs`` enumerates exactly the 10 retrieve fields."""
    result = retrieve_kwargs(_fake_config())

    assert set(result) == {
        "retrieve_url",
        "repo_id_dict",
        "page_num",
        "filter_string",
        "scope",
        "extra_repo_ids",
        "rerank_url",
        "rerank_batch_size",
        "score_threshold",
        "top_n",
    }
    assert result["retrieve_url"] == "https://example.invalid/retrieve"
    assert result["top_n"] == 5


def test_obs_kwargs_threads_credentials_through() -> None:
    """``obs_kwargs`` materialises the OBS storage block.

    Asserts both the credential pass-through (no re-reading sensitive
    inside) and the eight expected key names.
    """
    credentials = ("plain-access-key", "plain-secret")
    result = obs_kwargs(_fake_config(), credentials)

    assert result["access_key_id"] == "plain-access-key"
    assert result["secret_access_key"] == "plain-secret"
    assert set(result) == {
        "access_key_id",
        "secret_access_key",
        "obs_server",
        "bucket_name",
        "part_size",
        "task_num",
        "max_concurrency",
        "max_workers",
    }


def test_retry_kwargs_returns_three_documented_keys() -> None:
    """``retry_kwargs`` enumerates exactly timeout/retriable_codes/max_retries.

    Kept separate from ``chat_kwargs`` so non-chat handlers can spread
    retry kwargs without the chat block.
    """
    result = retry_kwargs(_fake_config())

    assert result == {
        "timeout": 30,
        "retriable_codes": [502, 503],
        "max_retries": 3,
    }


def test_coder_kwargs_pulls_from_sensitive_only() -> None:
    """``coder_kwargs`` returns CODER_URL / CODER_MODEL / CODER_API_KEY."""
    result = coder_kwargs(_fake_sensitive())

    assert result == {
        "model_url": "https://example.invalid/coder",
        "model_name": "fake-coder-model",
        "coder_api_key": "fake-coder-api-key",
    }


def test_analysis_platform_kwargs_has_four_documented_keys() -> None:
    """``analysis_platform_kwargs`` returns the platform identity block."""
    result = analysis_platform_kwargs(_fake_config())

    assert result == {
        "analysis_url": "https://example.invalid/analysis",
        "region": "region-a",
        "resource_dict": {"small": {}},
        "app_id_dict": {"app": "id"},
    }
