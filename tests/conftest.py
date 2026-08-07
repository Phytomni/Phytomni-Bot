# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared pytest fixtures for offline, secret-free test execution.

Defines layer marker hooks, fake secret environment setup, environment flag
helpers, and the autouse HTTP blocker fixture used by repository tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Self

import httpx
import pytest

from mcp_server_phytomni.agents.shared.citation_database import (
    resolve_citation_database_path,
)
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.config.settings import (
    get_sensitive_config,
)
from mcp_server_phytomni.func_cache.storage import Storage
from mcp_server_phytomni.runtime.request_context import (
    request_context,
)
from mcp_server_phytomni.storage import (
    obs_relay_ops as obs_relay_ops_module,
)
from tests.support.citation_database import create_valid_citation_database
from tests.support.http_fakes import open_asgi_client

# The repo-root ``conftest.py`` installs the offline test env before
# pytest reaches this module, so the imports above can stay at the
# top of the file (no E402 / C0413 / noqa needed). See that file's
# module docstring for why the install must precede every
# ``mcp_server_phytomni`` import.

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}

TEST_ROOT = Path(__file__).resolve().parent
DEMO_DATA_DIR = (TEST_ROOT.parent / "demo_data").resolve()
TEST_LAYER_MARKERS = {
    "unit": "unit",
    "server": "server",
    "agents": "agent",
    "integration": "integration",
}


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
def _reset_sensitive_config_cache() -> Iterator[None]:
    """Drop ``get_sensitive_config`` cache before every test.

    SensitiveConfig is now lazily cached process-wide via
    ``@lru_cache`` on ``get_sensitive_config``. Module-level imports
    of agent files (which still call ``SensitiveConfig.load()`` at
    import time on some paths) populate the cache before any test
    runs; tests that monkeypatch env vars or settings module attrs
    must see a fresh load. Clearing both before and after keeps
    each test independent of its neighbours and of import-time
    side effects.
    """
    get_sensitive_config.cache_clear()
    try:
        yield
    finally:
        get_sensitive_config.cache_clear()


@pytest.fixture(autouse=True)
def _reset_request_contextvars() -> Iterator[None]:
    """Bracket the three per-request contextvars so tests stay isolated.

    The submit chokepoint in ``mcp/handlers`` now calls ``bind_run_id``
    without an explicit reset — the HTTP middleware's ``finally`` block
    cleans the value up in production. In pytest there is no
    middleware, so a chokepoint binding leaks across tests unless we
    bracket the trio here. Using ``request_context(None, None, None)``
    matches what the middleware does on each request entry.
    """
    with request_context(None, None, None):
        yield


@pytest.fixture(autouse=True)
def _close_func_cache_storages() -> Iterator[None]:
    """Close registered cache connections after every test."""
    try:
        yield
    finally:
        Storage.close_all()


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


@pytest.fixture
def citation_db_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Provide one explicitly configured schema-v1 citation artifact."""
    path = create_valid_citation_database(tmp_path / "citation.sqlite")
    monkeypatch.setenv("CITATION_DB_PATH", str(path))
    resolve_citation_database_path.cache_clear()
    try:
        yield path
    finally:
        resolve_citation_database_path.cache_clear()


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


@pytest.fixture
def instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``asyncio.sleep`` to a no-op so retry backoff is instant.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def _no_sleep(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _build_scripted_client_class(
    behaviors: list[Any], calls: dict[str, int]
) -> type:
    """Return a scripted async HTTP client class shared by both fixtures.

    The class is an async context manager whose ``post`` replays one
    scripted behavior per call: an exception instance is raised,
    anything else is returned. ``calls["n"]`` counts POST attempts.
    """
    script = list(behaviors)

    class _FakeClient:
        """Async context-manager HTTP client stub."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Ignore client construction arguments."""
            del args, kwargs

        async def __aenter__(self) -> Self:
            """Enter the async context."""
            return self

        async def __aexit__(self, *args: Any) -> None:
            """Exit the async context."""
            del args

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            """Replay the next scripted behavior for one POST."""
            del args, kwargs
            calls["n"] += 1
            behavior = script.pop(0)
            if isinstance(behavior, BaseException):
                raise behavior
            return behavior

    return _FakeClient


def _build_async_factory(
    behaviors: list[Any], calls: dict[str, int]
) -> Callable[..., Any]:
    """Return a ``get_async_client``-shaped factory around the scripted client.

    The returned callable constructs the scripted client directly, so the
    production ``async with get_async_client()`` lifecycle remains explicit
    without a generator-based context-manager wrapper.
    """
    client_cls = _build_scripted_client_class(behaviors, calls)
    return client_cls


@pytest.fixture
def fake_async_factory() -> Callable[[list[Any], dict[str, int]], Any]:
    """Build a ``get_async_client`` substitute around the scripted client.

    The shared ``get_async_client`` factory yields an open client from
    an ``@asynccontextmanager``; tests that previously monkey-patched
    ``AsyncClient`` now monkey-patch ``get_async_client`` instead.
    This fixture wraps the scripted client class so callers do not
    have to redeclare the context-manager boilerplate at every call
    site.

    Returns:
        ``make(behaviors, calls) -> async-context-manager factory``.
    """
    return _build_async_factory


@pytest.fixture
def fake_client_factory() -> Callable[[list[Any], dict[str, int]], type]:
    """Return a builder for a scripted fake async HTTP client.

    The built class is an async context manager whose ``post`` replays
    one scripted behavior per call: an exception instance is raised,
    anything else is returned. ``calls["n"]`` counts POST attempts.

    Returns:
        ``make(behaviors, calls) -> type`` client-class builder.
    """

    def _make(behaviors: list[Any], calls: dict[str, int]) -> type:
        return _build_scripted_client_class(behaviors, calls)

    return _make


@pytest.fixture
async def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client wired to the FastAPI app over ASGI.

    The shared ASGI helper restores the real request method because
    ``httpx.ASGITransport`` dispatches in-process and never opens a socket.

    Args:
        monkeypatch: Pytest monkeypatch used to restore the real request.

    Returns:
        Async iterator yielding the bound httpx client.
    """
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.test"
    ) as client:
        yield client


@pytest.fixture
async def a2ui_client_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    """Yield two independent ASGI clients for one shared app state."""
    async with AsyncExitStack() as stack:
        first = await stack.enter_async_context(
            open_asgi_client(
                monkeypatch, create_app(), base_url="http://api.first"
            )
        )
        second = await stack.enter_async_context(
            open_asgi_client(
                monkeypatch, create_app(), base_url="http://api.second"
            )
        )
        yield first, second


@pytest.fixture
def issued_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the API key store at a temp db and return a fresh key.

    Args:
        tmp_path: Pytest temp directory for the throwaway SQLite store.
        monkeypatch: Used to set the store-path environment variable.

    Returns:
        A usable plaintext API key bound to user ``u1``.
    """
    db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    return ApiKeyStore(db).create(user_id="u1").api_key


@pytest.fixture
def chat_completion() -> Callable[..., Any]:
    """Return an async helper posting one chat completion request.

    The client is passed in at call time rather than injected, so this
    fixture does not shadow the ``api_client`` fixture.

    Returns:
        ``post(client, key, *, model, messages, content, **extra)``
        coroutine factory issuing the authenticated POST.
    """

    async def _post(
        client: httpx.AsyncClient,
        key: str,
        *,
        model: str = "phyto-chat",
        messages: Any = None,
        content: str = "hi",
        **extra: Any,
    ) -> httpx.Response:
        """Issue one authenticated chat completion request."""
        if messages is None:
            messages = [{"role": "user", "content": content}]
        body = {"model": model, "messages": messages, **extra}
        return await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
        )

    return _post


@pytest.fixture
def tasks_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Point the handlers' local task registry at a temp SQLite file.

    Shared by the submit-recording and GetTaskStatus suites so they do
    not duplicate the same ``resolve_tasks_db_path`` monkeypatch idiom
    (root conftest per the one-conftest-per-test-root convention).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        The temp database path the handlers will resolve.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.submit_recorder.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.resolve_tasks_db_path",
        lambda: db_path,
    )
    return db_path


@pytest.fixture(name="cache_db")
def cache_db_fixture(tmp_path: Path) -> Iterator[str]:
    """Yield a temporary func_cache database path and close its singleton.

    Shared by ``tests/unit/func_cache/test_cli.py`` and
    ``tests/unit/func_cache/test_maintenance.py`` (root conftest per
    the one-conftest-per-test-root convention).

    Args:
        tmp_path: Temporary directory provided by pytest.

    Yields:
        Path string for the test's ephemeral SQLite cache.
    """
    db_path = str(tmp_path / "cache.sqlite")
    yield db_path
    Storage.get_instance(db_path).close()


@pytest.fixture(name="cache_db_alpha_one_expired")
def cache_db_alpha_one_expired_fixture(cache_db: str) -> str:
    """Pre-seed ``cache_db`` with alpha k1 expired (``ttl=0``) and k2 live.

    The purge-expired tests in both ``test_cli`` and
    ``test_maintenance`` share the same seed: one expired row and one
    open-ended row under the same func.

    Args:
        cache_db: Temporary cache database path.

    Returns:
        The same ``cache_db`` path, seeded with the two-row mix.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)
    storage.set("alpha", "k2", b"v")
    return cache_db


@pytest.fixture(name="cache_db_two_funcs")
def cache_db_two_funcs_fixture(cache_db: str) -> str:
    """Pre-seed ``cache_db`` with alpha={k1,k2} and beta={k1}.

    Repeated across the stats / purge / reexpire tests in both
    ``test_cli`` and ``test_maintenance``; centralising the seed keeps
    every "two funcs, three live entries" assertion driven by one
    source of truth.

    Args:
        cache_db: Temporary cache database path.

    Returns:
        The same ``cache_db`` path, populated with three entries.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("alpha", "k2", b"v")
    storage.set("beta", "k1", b"v")
    return cache_db


def _build_fake_obs_client() -> Any:
    """Return a fresh OBS SDK stand-in class with class-level capture state.

    Two fixtures share this helper: ``fake_obs_client_factory`` exposes
    it raw for unit tests that want to control creation timing, and
    ``fake_obs_client`` patches ``storage.obs_relay_ops.ObsClient`` for
    server tests that exercise the relay SDK seam. Each call yields a new
    class so capture state never leaks
    between cases.
    """

    class _FakeStreamReader:
        """Chunked reader stand-in for ``getObject`` stream mode.

        Walks the seeded object bytes in ``read(size)``-sized slices so a
        relay download test exercises the streaming path without a real
        OBS connection.
        """

        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0
            self.closed = False

        def read(self, size: int = -1) -> bytes:
            """Return up to ``size`` bytes from the current position."""
            if size is None or size < 0:
                chunk = self._data[slice(self._pos, None)]
                self._pos = len(self._data)
                return chunk
            chunk = self._data[slice(self._pos, self._pos + size)]
            self._pos += len(chunk)
            return chunk

        def close(self) -> None:
            """Mark the stream closed (the SDK closes the connection)."""
            self.closed = True

    class _FakeObsClient:
        """Capture-only OBS SDK stand-in for the SDK fallback path.

        Supports ``putContent`` (used by the upload write seam),
        ``getObject`` (writes seeded ``objects`` bytes to ``downloadPath``,
        or streams them through ``body.response`` when loaded out of
        memory), ``getObjectMetadata`` (reports the seeded byte length),
        and ``listObjects`` (returns seeded ``pages`` in order). Tests seed
        ``objects`` / ``pages`` before exercising the download/list ops.
        Class-level state is intentional: each fixture call returns a fresh
        class so one simulated OBS lifecycle can retain objects, page order,
        and captured SDK kwargs across client instances.
        """

        captured: dict[str, Any] = {}
        objects: dict[str, bytes] = {}
        pages: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            _FakeObsClient.captured = {"init": kwargs}

        @classmethod
        def reset_state(cls) -> None:
            """Clear captured calls and seeded OBS objects between tests."""
            cls.captured = {}
            cls.objects = {}
            cls.pages = []

        def __getattr__(self, name: str) -> Any:
            """Map OBS SDK camelCase methods to snake-case fakes."""
            sdk_ops = {
                "putContent": self._put_content,
                "getObject": self._get_object,
                "getObjectMetadata": self._get_object_metadata,
                "listObjects": self._list_objects,
            }
            if name in sdk_ops:
                return sdk_ops[name]
            raise AttributeError(name)

        def _put_content(self, **kwargs: Any) -> Any:
            _FakeObsClient.captured["put_content"] = kwargs
            return SimpleNamespace(
                status=200,
                requestId="request-id",
                errorCode=None,
            )

        def _get_object(self, **kwargs: Any) -> Any:
            _FakeObsClient.captured["get_object"] = kwargs
            buffer = _FakeObsClient.objects.get(kwargs["objectKey"], b"")
            download_path = kwargs.get("downloadPath")
            if download_path:
                Path(download_path).write_bytes(buffer)
                return SimpleNamespace(
                    status=200,
                    body=SimpleNamespace(buffer=buffer),
                    requestId="request-id",
                )
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(response=_FakeStreamReader(buffer)),
                requestId="request-id",
            )

        def _get_object_metadata(self, **kwargs: Any) -> Any:
            _FakeObsClient.captured["get_object_metadata"] = kwargs
            buffer = _FakeObsClient.objects.get(kwargs["objectKey"], b"")
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(contentLength=len(buffer)),
                requestId="request-id",
            )

        def _list_objects(self, **kwargs: Any) -> Any:
            calls = _FakeObsClient.captured.setdefault("list_calls", [])
            calls.append(kwargs)
            return SimpleNamespace(
                status=200,
                body=_FakeObsClient.pages.pop(0),
                requestId="request-id",
            )

    _FakeObsClient.reset_state()
    return _FakeObsClient


@pytest.fixture
def fake_obs_client_factory() -> Callable[..., Any]:
    """Expose ``_build_fake_obs_client`` as a per-test factory.

    Tests bind the returned class with
    ``monkeypatch.setattr(module, "ObsClient", fake)`` and then read
    ``fake.captured`` (a dict) to assert OBS init kwargs and
    ``putContent`` call arguments. The return type is ``Any`` so static
    checkers do not lose ``captured`` to the bare ``type`` upcast.

    Returns:
        Factory returning a new class on each call.
    """
    return _build_fake_obs_client


@pytest.fixture
def fake_obs_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch ``storage.obs_relay_ops.ObsClient`` with a capturing fake.

    Yields the patched class so tests can inspect ``.captured`` for
    OBS init kwargs and ``putContent`` call arguments. Defined in
    conftest (not the test file) so test parameters of the same name
    do not trigger pylint W0621 redefined-outer-name.

    Args:
        monkeypatch: Pytest monkeypatch used to bind the fake into
            ``storage.obs_relay_ops`` for relay object tests.

    Returns:
        The patched fake OBS client class.
    """
    fake = _build_fake_obs_client()
    monkeypatch.setattr(obs_relay_ops_module, "ObsClient", fake)
    return fake
