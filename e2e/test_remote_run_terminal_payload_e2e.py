# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Live e2e: four result-producing runs publish one result archive.

Submits the committed Analyst, Research, Network, and Design payloads through
their native HTTP routes, polls the owner-scoped run, then verifies the
published ZIP through a Bot-side authenticated test helper.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .helpers.api_server import (
    ApiServer,
    auth_header,
    boot_phytomni_api,
    make_async_client,
)
from .helpers.assertions import (
    assert_remote_run_terminal_payload,
    assert_result_archive_members,
    fetch_authenticated_result_archive,
)
from .helpers.polling import poll_http_run_to_terminal

pytestmark = pytest.mark.live


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


@pytest.mark.parametrize(
    ("slug", "payload_name"),
    [
        ("analyst", "analyst_agent.json"),
        ("research", "in_silico_research_agent.json"),
        ("network", "gene_network_agent.json"),
        ("design", "digital_design_agent.json"),
    ],
)
async def test_remote_run_terminal_payload_e2e(
    terminal_api_client: httpx.AsyncClient,
    terminal_api_server: ApiServer,
    load_payload: Callable[[str], dict[str, Any]],
    slug: str,
    payload_name: str,
) -> None:
    """Each result-producing Agent publishes one nonempty result archive.

    Args:
        terminal_api_client: Bound async HTTP client.
        terminal_api_server: Running API details.
        load_payload: Loader returning one committed Agent payload.
        slug: Canonical native Agent slug.
        payload_name: Matching committed payload filename.
    """
    payload = load_payload(payload_name)

    submit = await terminal_api_client.post(
        f"/v1/agents/{slug}/runs",
        json={"arguments": payload},
        headers=auth_header(terminal_api_server),
    )
    assert submit.status_code in (
        200,
        202,
    ), f"{slug} submit returned {submit.status_code}"
    run_id = submit.json().get("id")
    assert run_id, f"{slug} submit returned no run id"

    terminal = await poll_http_run_to_terminal(
        terminal_api_client,
        str(run_id),
        headers=auth_header(terminal_api_server),
    )
    assert terminal.status == "succeeded"
    assert_remote_run_terminal_payload(terminal.result, agent=slug)
    status = await terminal_api_client.get(
        f"/v1/runs/{run_id}", headers=auth_header(terminal_api_server)
    )
    assert status.status_code == 200
    archive = fetch_authenticated_result_archive(
        status.json(),
        authenticated_user=terminal_api_server.user_id,
        agent=slug,
    )
    assert_result_archive_members(archive)
