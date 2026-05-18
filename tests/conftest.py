# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared pytest fixtures for offline, secret-free test execution.

Defines layer marker hooks, fake secret environment setup, environment flag
helpers, and the autouse HTTP blocker fixture used by repository tests.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

TEST_ROOT = Path(__file__).resolve().parent
DEMO_DATA_DIR = (TEST_ROOT.parent / "demo_data").resolve()
TEST_LAYER_MARKERS = {
    "unit": "unit",
    "server": "server",
    "agents": "agent",
    "integration": "integration",
}
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

_TEST_ENV = {
    "DOMAIN_NAME": "pytest-domain",
    "USER_NAME": "pytest-user",
    "USER_PASSWORD": "pytest-password",
    "ACCESS_KEY_ID": "pytest-access-key-id",
    "SECRET_ACCESS_KEY": "pytest-secret-access-key",
    "BASE_URL": "https://example.invalid/llm",
    "MODEL_ID": "pytest-model",
    "API_KEY": "pytest-api-key",
    "CODER_URL": "https://example.invalid/coder",
    "CODER_MODEL": "pytest-coder-model",
    "CODER_API_KEY": "pytest-coder-api-key",
    "EMBED_URL": "https://example.invalid/embed",
    "EMBED_MODEL": "pytest-embed-model",
    "EMBED_API_KEY": "pytest-embed-api-key",
    "BI_TOKEN": "pytest-bi-token",
}


def _install_test_environment() -> None:
    """Verify install test environment."""
    os.environ["PHYTOMNI_TESTING"] = "1"
    for name, value in _TEST_ENV.items():
        os.environ[name] = value


_install_test_environment()


def _env_flag_enabled(name: str) -> bool:
    """Verify env flag enabled."""
    value = os.environ.get(name, "")
    return value.lower() in TRUTHY_ENV_VALUES


def _layer_marker_for_item(item: pytest.Item) -> str | None:
    """Verify layer marker for item."""
    try:
        relative_path = Path(item.path).resolve().relative_to(TEST_ROOT)
    except ValueError:
        return None

    if not relative_path.parts:
        return None

    return TEST_LAYER_MARKERS.get(relative_path.parts[0])


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply repository test layer markers and external-service guards.

    Args:
        items: Collected pytest items to mark or skip.
    """
    allow_integration = _env_flag_enabled("PHYTOMNI_RUN_INTEGRATION")
    allow_network = _env_flag_enabled("PHYTOMNI_ALLOW_NETWORK")

    for item in items:
        layer_marker = _layer_marker_for_item(item)
        if layer_marker is not None:
            item.add_marker(getattr(pytest.mark, layer_marker))

        if item.get_closest_marker("integration") and not allow_integration:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "Set PHYTOMNI_RUN_INTEGRATION=1 to run integration "
                        "tests that may need real external services."
                    )
                )
            )

        if item.get_closest_marker("network") and not allow_network:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "Set PHYTOMNI_ALLOW_NETWORK=1 to run tests that may "
                        "perform external network calls."
                    )
                )
            )


@pytest.fixture(autouse=True)
def block_external_http(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Block accidental HTTP calls unless a test opts into ``network``.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace network APIs.
        request: Current pytest fixture request.

    Returns:
        Iterator that yields once while outbound HTTP is blocked.
    """
    if request.node.get_closest_marker("network"):
        yield
        return

    def blocked_create_connection(*args: Any, **kwargs: Any) -> Any:
        """Raise for socket connection attempts in offline tests.

        Args:
            *args: Ignored positional socket arguments.
            **kwargs: Ignored keyword socket arguments.

        Returns:
            Never returns; always raises RuntimeError.
        """
        raise RuntimeError(
            "External network access is disabled for default pytest runs. "
            "Mark the test with @pytest.mark.network to opt in."
        )

    monkeypatch.setattr(socket, "create_connection", blocked_create_connection)

    def blocked_request(*args: Any, **kwargs: Any) -> Any:
        """Raise for sync HTTP requests in offline tests.

        Args:
            *args: Ignored positional HTTP arguments.
            **kwargs: Ignored keyword HTTP arguments.

        Returns:
            Never returns; always raises RuntimeError.
        """
        raise RuntimeError(
            "HTTP requests are disabled for default pytest runs. "
            "Mark the test with @pytest.mark.network to opt in."
        )

    async def blocked_async_request(*args: Any, **kwargs: Any) -> Any:
        """Raise for async HTTP requests in offline tests.

        Args:
            *args: Ignored positional HTTP arguments.
            **kwargs: Ignored keyword HTTP arguments.

        Returns:
            Never returns; always raises RuntimeError.
        """
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


@pytest.fixture(scope="session")
def demo_data_dir() -> Path:
    """Return the absolute path to the committed demo_data/ directory.

    Returns:
        Absolute path to the repository's demo_data root.
    """
    return DEMO_DATA_DIR


@pytest.fixture(scope="session")
def secret_scanner() -> ModuleType:
    """Load scripts/scan_secrets.py once as an importable module.

    The secret scanner ships as a standalone script rather than a
    package module, so tests load it via importlib. Anchoring the path
    on TEST_ROOT keeps a single, depth-independent source of truth for
    every test directory instead of a per-file ``parents[N]`` walk.

    Returns:
        The imported scan_secrets module object.
    """
    script_path = TEST_ROOT.parent / "scripts" / "scan_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_secrets", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scan_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module
