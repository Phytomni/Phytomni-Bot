# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared helpers for booting ``phytomni-api`` from a live e2e test.

Extracts the subprocess boot, key minting, health-gate, and httpx
client setup that originally lived inline in ``test_api_http_e2e.py``
so both live HTTP e2e tests (``test_api_http_e2e.py`` and
``test_concurrent_http_e2e.py``) can spin up the same uvicorn process
without duplicating ~150 LOC.

Public surface:

* ``ApiServer`` -- connection details for one running API subprocess.
* ``boot_phytomni_api(tmp_path_factory)`` -- pytest session-scoped
  context manager yielding an ``ApiServer``.
* ``make_async_client(api_server)`` -- async context manager yielding
  an ``httpx.AsyncClient`` bound to the live base URL.
* ``auth_header(api_server)`` -- Bearer header dict.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import IO, NamedTuple

import httpx
import pytest

from mcp_server_phytomni.api.auth import ApiKeyStore

_STARTUP_DEADLINE_DEFAULT = 120.0
_READ_TIMEOUT_DEFAULT = 1200.0
_LOG_TAIL = 500
_E2E_SERVICE_TOKEN = "e2e-service-token"
_DEFAULT_APP_MODULE = "mcp_server_phytomni.api.server"


class ApiServer(NamedTuple):
    """Connection details for one live ``phytomni-api`` subprocess.

    Attributes:
        base_url: Root URL the uvicorn process is bound to.
        api_key: One-time plaintext key minted for this session.
        user_id: The user the key authenticates.
        service_token: Service-principal token wired into the subprocess
            env via ``PHYTOMNI_API_SERVICE_TOKEN`` so admin routes
            (``/v1/api-keys/*``) and delegated lookups
            (``GET /v1/runs?user_id=``) are exercisable from the same
            fixture without a second boot.
    """

    base_url: str
    api_key: str
    user_id: str
    service_token: str


def auth_header(server: ApiServer) -> dict[str, str]:
    """Return the Bearer auth header for the issued key.

    Args:
        server: Running API details.

    Returns:
        Mapping with a single ``Authorization`` entry suitable for
        ``httpx`` requests.
    """
    return {"Authorization": f"Bearer {server.api_key}"}


def service_auth_header(server: ApiServer) -> dict[str, str]:
    """Return the ``X-Service-Token`` header for admin-scope calls.

    Using the dedicated header lets a request carry both the user key
    (Authorization) and the service token at the same time, matching
    how the admin routes are accessed in production (Web ops keeps a
    user key for normal traffic but elevates per-call via the header).

    Args:
        server: Running API details.

    Returns:
        Mapping with a single ``X-Service-Token`` entry.
    """
    return {"X-Service-Token": server.service_token}


def _free_port() -> int:
    """Reserve and release an ephemeral localhost port.

    Returns:
        A port free at call time; the startup health-gate tolerates the
        brief race between releasing and the child re-binding it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _startup_deadline_seconds() -> float:
    """Return the startup health-gate budget.

    Returns:
        Deadline in seconds. Overrideable via
        ``PHYTOMNI_E2E_API_STARTUP_SECONDS``.
    """
    raw = os.environ.get("PHYTOMNI_E2E_API_STARTUP_SECONDS")
    return float(raw) if raw else _STARTUP_DEADLINE_DEFAULT


def _read_timeout_seconds() -> float:
    """Return the per-request read timeout.

    Returns:
        Read timeout in seconds. Overrideable via
        ``PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS``. Review/BriefGene
        block the synchronous endpoint up to ~10 min, so the default
        is 20 min.
    """
    raw = os.environ.get("PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS")
    return float(raw) if raw else _READ_TIMEOUT_DEFAULT


def _drain(stream: IO[str], sink: deque[str]) -> None:
    """Copy a subprocess stream line-by-line into a bounded buffer.

    Reading the pipe continuously prevents a full OS buffer from
    stalling a 10-min server; only the most recent lines are kept.

    Args:
        stream: The child's merged stdout/stderr text stream.
        sink: Bounded buffer retaining the most recent log lines.
    """
    for line in stream:
        sink.append(line.rstrip("\n"))


def _log_tail(logs: deque[str]) -> str:
    """Return the captured subprocess log tail as one string."""
    return "\n".join(logs)


def _await_healthy(
    proc: subprocess.Popen[str], base_url: str, logs: deque[str]
) -> None:
    """Block until ``/healthz`` is ok or the deadline elapses.

    Args:
        proc: The running API subprocess (checked for early exit).
        base_url: Base URL the service is expected to bind.
        logs: Captured log tail surfaced on failure.

    Raises:
        RuntimeError: If the subprocess exits or never becomes healthy.
    """
    deadline = time.monotonic() + _startup_deadline_seconds()
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "API subprocess exited during startup "
                f"(code {proc.returncode}); logs:\n{_log_tail(logs)}"
            )
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{base_url}/healthz")
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"API did not become healthy in time; logs:\n{_log_tail(logs)}"
    )


def _api_command(app_module: str, port: int) -> list[str]:
    """Return the default launcher or a test-only ASGI module command."""
    if app_module == _DEFAULT_APP_MODULE:
        return [sys.executable, "-m", app_module]
    return [
        sys.executable,
        "-m",
        "uvicorn",
        f"{app_module}:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


@contextmanager
def boot_phytomni_api(
    tmp_path_factory: pytest.TempPathFactory,
    *,
    app_module: str = _DEFAULT_APP_MODULE,
    environment: Mapping[str, str] | None = None,
) -> Generator[ApiServer, None, None]:
    """Boot the HTTP API subprocess and yield its connection details.

    Mints a throwaway per-user API key in a temp SQLite store, starts
    ``mcp_server_phytomni.api.server`` on an ephemeral port, blocks
    until ``/healthz`` is ok, and tears down on context exit.

    Args:
        tmp_path_factory: Pytest temp-dir factory for the throwaway
            SQLite stores.
        app_module: Importable module launched in the API subprocess.
        environment: Test-only environment additions applied before the
            helper pins its host, port, key store, and task database.

    Yields:
        Connection details for the running API process.
    """
    store_dir = tmp_path_factory.mktemp("api")
    keys_db = str((store_dir / "api_keys.sqlite").resolve())
    runs_db = str((store_dir / "api_runs.sqlite").resolve())
    user_id = "phytomni-api-e2e"
    created = ApiKeyStore(keys_db).create(user_id=user_id, name="api-http-e2e")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(environment or {})
    env.pop("PHYTOMNI_TESTING", None)
    env["API_HOST"] = "127.0.0.1"
    env["API_PORT"] = str(port)
    env["PHYTOMNI_API_KEYS_DB"] = keys_db
    # ``ApiConfig.API_TASKS_DB_PATH`` accepts ``API_TASKS_DB_PATH`` or
    # ``PHYTOMNI_TASKS_DB``; ``PHYTOMNI_API_RUNS_DB`` is *not* recognised
    # and was being silently dropped, falling back to the default
    # ``server_tasks.db`` in the repo root and polluting the working tree
    # across runs.
    env["PHYTOMNI_TASKS_DB"] = runs_db
    env["PHYTOMNI_API_SERVICE_TOKEN"] = _E2E_SERVICE_TOKEN

    cmd = _api_command(app_module, port)
    logs: deque[str] = deque(maxlen=_LOG_TAIL)
    with subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        drain = threading.Thread(
            target=_drain, args=(proc.stdout, logs), daemon=True
        )
        drain.start()
        try:
            _await_healthy(proc, base_url, logs)
            yield ApiServer(
                base_url, created.api_key, user_id, _E2E_SERVICE_TOKEN
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    drain.join(timeout=5)


@asynccontextmanager
async def make_async_client(
    api_server: ApiServer,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an ``httpx.AsyncClient`` bound to the live API base URL.

    Args:
        api_server: Running API details.

    Yields:
        Async client with a long read timeout and no default auth
        header (so per-call auth shape stays explicit).
    """
    timeout = httpx.Timeout(
        connect=5.0,
        read=_read_timeout_seconds(),
        write=10.0,
        pool=5.0,
    )
    async with httpx.AsyncClient(
        base_url=api_server.base_url, timeout=timeout
    ) as client:
        yield client
