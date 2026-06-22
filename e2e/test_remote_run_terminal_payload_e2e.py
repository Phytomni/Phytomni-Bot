# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Live e2e: a terminal remote run carries a renderable answer + paths.

Submits the committed gene_network payload through the HTTP API
(``POST /v1/agents/network/runs``), polls ``GET /v1/runs/{id}`` until the
run reaches a terminal state, and asserts the run-level settle assembled a
``result.formatted.answer`` plus concrete ``result.artifacts[].paths`` --
the contract ``assert_remote_run_terminal_payload`` pins. This is the only
e2e that exercises the HTTP run-level terminal payload: the per-agent
suites poll ``server_tasks.db`` at the task level and never observe this
surface, so without this test the run-level assembly seam is unverified
against a live backend.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .helpers.api_server import (
    ApiServer,
    boot_phytomni_api,
    make_async_client,
    service_auth_header,
)
from .helpers.assertions import assert_remote_run_terminal_payload

pytestmark = pytest.mark.live

_TERMINAL_STATUSES = frozenset(
    {"succeeded", "success", "completed", "done", "failed", "error"}
)
_POLL_TIMEOUT_SECONDS = 1800.0
_POLL_INTERVAL_SECONDS = 10.0


@pytest.fixture(scope="session", name="terminal_api_server")
def terminal_api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the HTTP API subprocess once for the terminal-payload test.

    Args:
        tmp_path_factory: Session tmp-dir factory for the throwaway
            SQLite stores.

    Yields:
        Live API connection details.
    """
    with boot_phytomni_api(tmp_path_factory) as server:
        yield server


@pytest_asyncio.fixture(name="terminal_api_client")
async def terminal_api_client_fixture(
    terminal_api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an async HTTP client bound to the terminal-payload server.

    Args:
        terminal_api_server: Running API details.

    Yields:
        ``httpx.AsyncClient`` configured with a long read timeout.
    """
    async with make_async_client(terminal_api_server) as client:
        yield client


async def _poll_run_to_terminal(
    client: httpx.AsyncClient,
    server: ApiServer,
    run_id: str,
) -> dict[str, Any]:
    """Poll ``GET /v1/runs/{id}`` until terminal and return its ``result``.

    Args:
        client: Bound async HTTP client.
        server: Running API details (for the service auth header).
        run_id: Run id returned by the remote submit.

    Returns:
        The terminal run's ``result`` object (empty dict if absent).

    Raises:
        AssertionError: On a non-200 poll or if the run never settles
            within ``_POLL_TIMEOUT_SECONDS``.
    """
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = await client.get(
            f"/v1/runs/{run_id}", headers=service_auth_header(server)
        )
        assert (
            resp.status_code == 200
        ), f"/v1/runs/{run_id} returned {resp.status_code}: {resp.text}"
        body = resp.json()
        if str(body.get("status", "")).lower() in _TERMINAL_STATUSES:
            result = body.get("result")
            return result if isinstance(result, dict) else {}
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"run {run_id} did not reach a terminal state within "
        f"{_POLL_TIMEOUT_SECONDS}s"
    )


async def test_remote_run_terminal_payload_e2e(
    terminal_api_client: httpx.AsyncClient,
    terminal_api_server: ApiServer,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """A terminal network run surfaces formatted.answer + artifact paths.

    Args:
        terminal_api_client: Bound async HTTP client.
        terminal_api_server: Running API details.
        load_payload: Loader returning the committed network payload.
    """
    payload = load_payload("gene_network_agent.json")

    submit = await terminal_api_client.post(
        "/v1/agents/network/runs",
        json=payload,
        headers=service_auth_header(terminal_api_server),
    )
    assert submit.status_code in (
        200,
        202,
    ), f"network submit returned {submit.status_code}: {submit.text}"
    run_id = submit.json().get("id")
    assert run_id, f"network submit returned no run id: {submit.text}"

    result = await _poll_run_to_terminal(
        terminal_api_client, terminal_api_server, str(run_id)
    )
    assert_remote_run_terminal_payload(result)
