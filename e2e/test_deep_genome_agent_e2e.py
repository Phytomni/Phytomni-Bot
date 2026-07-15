# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live DeepGenome acceptance through the long-lived HTTP run surface.

The HTTP service owns the coordinator process, so this test submits one
``deep_genome`` run, follows its owner-scoped status endpoint, records report
revision changes, and validates the complete terminal matrix. A failed
post-profile run is accepted only when it preserves ``intermediate_report``;
BriefGene failure is the one terminal path with no report.
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
from .helpers.assertions import assert_deep_genome_terminal
from .helpers.polling import (
    poll_http_run_to_terminal,
    task_state_from_mapping,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="session", name="deep_genome_api_server")
def deep_genome_api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the API process that owns the DeepGenome coordinator."""
    with boot_phytomni_api(tmp_path_factory) as server:
        yield server


@pytest_asyncio.fixture(name="deep_genome_api_client")
async def deep_genome_api_client_fixture(
    deep_genome_api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a long-read HTTP client bound to the coordinator service."""
    async with make_async_client(deep_genome_api_server) as client:
        yield client


async def test_deep_genome_agent_e2e_polls_http_terminal_report(
    deep_genome_api_client: httpx.AsyncClient,
    deep_genome_api_server: ApiServer,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """DeepGenome reaches terminal with a state-machine-valid report."""
    payload = load_payload("deep_genome_agent.json")
    submit = await deep_genome_api_client.post(
        "/v1/agents/deep_genome/runs",
        json={"arguments": payload},
        headers=auth_header(deep_genome_api_server),
    )
    assert (
        submit.status_code == 202
    ), f"DeepGenome submit returned {submit.status_code}"
    run_id = submit.json().get("id")
    assert isinstance(run_id, str) and run_id

    terminal = await poll_http_run_to_terminal(
        deep_genome_api_client,
        run_id,
        headers=auth_header(deep_genome_api_server),
    )
    state_payload = {**terminal.result, "status": terminal.status}
    state = task_state_from_mapping(state_payload, task_id=run_id)
    assert terminal.revisions, "DeepGenome exposed no report revision"
    assert terminal.revisions == tuple(sorted(set(terminal.revisions)))
    assert_deep_genome_terminal(state)

    if terminal.status == "succeeded":
        assert "# Deep Genome Analysis of" in (state.final_report or "")
        assert "## Gene Profiles" in (state.final_report or "")
        assert "## Bioinformatic Analysis" in (state.final_report or "")
