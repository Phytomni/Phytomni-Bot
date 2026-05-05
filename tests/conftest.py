# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared pytest fixtures for offline, secret-free test execution."""

from __future__ import annotations

import os
import socket
import importlib
from collections.abc import Iterator
from typing import Any

import pytest

_TEST_ENV = {
    "DOMAIN_NAME": "pytest-domain",
    "USER_NAME": "pytest-user",
    "USER_PASSWORD": "pytest-password",
    "AccessKeyID": "pytest-access-key-id",
    "SecretAccessKey": "pytest-secret-access-key",
    "BASE_URL": "https://example.invalid/llm",
    "MODEL_ID": "pytest-model",
    "API_KEY": "pytest-api-key",
    "CODER_URL": "https://example.invalid/coder",
    "CODER_MODEL": "pytest-coder-model",
    "CODER_API_KEY": "pytest-coder-api-key",
    "BI_TOKEN": "pytest-bi-token",
}


def _skip_real_env_file() -> bool:
    return True


def _install_test_environment() -> None:
    os.environ.setdefault("PHYTOMNI_TESTING", "1")
    for name, value in _TEST_ENV.items():
        os.environ.setdefault(name, value)


_install_test_environment()

_settings = importlib.import_module("mcp_server_phytomni.config.settings")
_settings.load_env_file = _skip_real_env_file
_settings.SensitiveConfig.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def block_external_http(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Block accidental HTTP calls unless a test opts into `network`."""
    if request.node.get_closest_marker("network"):
        yield
        return

    def blocked_create_connection(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "External network access is disabled for default pytest runs. "
            "Mark the test with @pytest.mark.network to opt in."
        )

    monkeypatch.setattr(socket, "create_connection", blocked_create_connection)

    try:
        import httpx
    except ImportError:
        httpx = None

    if httpx is not None:

        def blocked_request(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                "HTTP requests are disabled for default pytest runs. "
                "Mark the test with @pytest.mark.network to opt in."
            )

        async def blocked_async_request(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                "HTTP requests are disabled for default pytest runs. "
                "Mark the test with @pytest.mark.network to opt in."
            )

        monkeypatch.setattr(httpx.Client, "request", blocked_request)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "request",
            blocked_async_request,
        )

    yield
