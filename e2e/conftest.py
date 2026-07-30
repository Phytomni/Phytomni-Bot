# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pytest fixtures for the live business E2E suite.

Provides three pillars used by every test under ``e2e/``:

* ``mcp_client`` -- a session-scoped ``PhytomniMcpClient`` connected
  via stdio to the default ``mcp_server_phytomni.server`` module.
* ``published_demo_data`` -- mirrors every ``demo_data/docs/*`` and
  ``demo_data/sequences/*`` file to a per-session OBS prefix so live
  tests can reference real uploaded paths.
* ``load_payload(name, published=...)`` -- reads a committed payload
  from ``demo_data/payloads/`` and rewrites any
  ``/obs/phytomni/demo/...`` placeholder to the corresponding URL in
  the ``published`` map.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from mcp_client_phytomni import PhytomniMcpClient
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity

from .helpers.client import make_client
from .helpers.obs_publish import publish_demo_data

DEMO_PLACEHOLDER_PREFIX = "/obs/phytomni/demo/"


@pytest.fixture(scope="session", name="demo_data_dir")
def demo_data_dir_fixture() -> Path:
    """Return the absolute path to the committed ``demo_data/``.

    Returns:
        Absolute path to the repository's demo_data root.
    """
    return (Path(__file__).resolve().parent.parent / "demo_data").resolve()


@pytest.fixture(scope="session", name="session_run_identity")
def session_run_identity_fixture() -> RunIdentity:
    """Return a session-scoped ``RunIdentity`` used to scope uploads.

    Returns:
        Session-stable RunIdentity instance.
    """
    return RunIdentity.create(
        user_id="phytomni-e2e",
        id_factory=IdFactory(),
    )


@pytest.fixture(scope="session", name="published_demo_data")
def published_demo_data_fixture(
    demo_data_dir: Path,
    session_run_identity: RunIdentity,
) -> dict[str, str]:
    """Publish demo_data fixtures and return a rel-path → OBS URL map.

    Args:
        demo_data_dir: Source ``demo_data/`` directory.
        session_run_identity: Session-scoped identity used to scope the
            OBS prefix.

    Returns:
        Mapping from local relative path (``docs/sample.pdf``) to the
        published ``/obs/<bucket>/<key>`` URL.
    """
    return publish_demo_data(demo_data_dir, session_run_identity)


@pytest_asyncio.fixture(name="mcp_client")
async def mcp_client_fixture() -> AsyncIterator[PhytomniMcpClient]:
    """Yield a connected ``PhytomniMcpClient`` for one test.

    Function-scoped so the stdio subprocess starts fresh per test.
    The outer ``try``/``except`` swallows the known anyio +
    pytest-asyncio interaction where ``stdio_client`` raises
    ``RuntimeError: Attempted to exit cancel scope in a different
    task`` during teardown, because pytest-asyncio drives the
    generator's final ``__anext__`` from a task distinct from the
    one that entered the cancel scope. The server subprocess is
    still reaped when its stdio pipes close as pytest moves on, so
    the test outcome is reliable.

    Yields:
        Connected client whose server subprocess is shut down on
        teardown (best effort).
    """
    try:
        async with make_client() as client:
            yield client
    except RuntimeError as exc:
        if "different task" not in str(exc):
            raise


@pytest.fixture(name="load_payload")
def load_payload_fixture(
    demo_data_dir: Path,
    published_demo_data: Mapping[str, str],
) -> Callable[[str], dict[str, Any]]:
    """Return a callable that loads and rewrites a committed payload.

    Args:
        demo_data_dir: Resolved ``demo_data/`` directory.
        published_demo_data: Per-session mapping of local relative
            paths to their published OBS URLs.

    Returns:
        Callable accepting a payload filename (e.g. ``"chat_agent.json"``)
        and returning the parsed dict with every
        ``/obs/phytomni/demo/...`` placeholder rewritten to the
        corresponding URL in ``published_demo_data``.
    """

    def _load(name: str) -> dict[str, Any]:
        raw = (demo_data_dir / "payloads" / name).read_text(encoding="utf-8")
        payload = json.loads(raw)
        return _rewrite_obs_placeholders(payload, published_demo_data)

    return _load


def _rewrite_obs_placeholders(
    payload: Any,
    published: Mapping[str, str],
) -> Any:
    """Recursively rewrite placeholder OBS paths in ``payload``."""
    if isinstance(payload, str):
        return _rewrite_str(payload, published)
    if isinstance(payload, list):
        return [_rewrite_obs_placeholders(item, published) for item in payload]
    if isinstance(payload, dict):
        return {
            (
                _rewrite_str(key, published) if isinstance(key, str) else key
            ): _rewrite_obs_placeholders(value, published)
            for key, value in payload.items()
        }
    return payload


def _rewrite_str(value: str, published: Mapping[str, str]) -> str:
    """Rewrite one string if it starts with the demo placeholder prefix."""
    if not value.startswith(DEMO_PLACEHOLDER_PREFIX):
        return value
    relative = value.removeprefix(DEMO_PLACEHOLDER_PREFIX)
    return published.get(relative, value)
