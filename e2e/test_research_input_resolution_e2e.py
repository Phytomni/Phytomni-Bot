# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Manually gated live acceptance for Research input resolution.

This test has one and only one successful scientific-child budget.  Its single
request carries all three accepted pasted-reference grammars, then verifies an
exact replay and a same-key conflict without accepting another child.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
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
from .helpers.research_input_resolution import (
    ResearchInputE2eConfig,
    assert_redacted_evidence,
    build_three_form_query,
    load_research_input_e2e_config,
    research_input_e2e_enabled,
    sanitize_evidence,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not research_input_e2e_enabled(),
        reason=(
            "Research-input e2e requires PHYTOMNI_RUN_INTEGRATION=1, "
            "PHYTOMNI_ALLOW_NETWORK=1, and "
            "PHYTOMNI_E2E_RESEARCH_INPUT=1"
        ),
    ),
]

_NONTERMINAL_STAGES = {
    "input_resolution",
    "planning",
    "execution",
    "report_assembly",
}
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@pytest.fixture(scope="session", name="research_input_api_server")
def research_input_api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
    research_input_e2e_config: ResearchInputE2eConfig,
) -> Iterator[ApiServer]:
    """Boot an isolated local API only after all live gates permit it."""
    del research_input_e2e_config
    with boot_phytomni_api(tmp_path_factory) as server:
        yield server


@pytest_asyncio.fixture(name="research_input_api_client")
async def research_input_api_client_fixture(
    research_input_api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an authenticated client for the isolated local API."""
    async with make_async_client(research_input_api_server) as client:
        yield client


@pytest.fixture(scope="session", name="research_input_e2e_config")
def research_input_e2e_config_fixture() -> ResearchInputE2eConfig:
    """Validate operator-provided synthetic references before the API boots."""
    return load_research_input_e2e_config()


async def test_research_input_resolution_e2e(
    research_input_api_client: httpx.AsyncClient,
    research_input_api_server: ApiServer,
    research_input_e2e_config: ResearchInputE2eConfig,
) -> None:
    """Replay, conflict, stages, and one bounded child all stay redacted."""
    run_id = await _admit_replay_and_assert_conflict(
        research_input_api_client,
        research_input_api_server,
        research_input_e2e_config,
    )
    config = research_input_e2e_config
    terminal, stages = await _await_research_terminal(
        research_input_api_client,
        research_input_api_server,
        run_id,
        config.timeout_seconds,
        config,
    )
    evidence = sanitize_evidence(run_id=run_id, stages=stages, config=config)
    assert terminal == "succeeded", evidence
    assert stages, evidence
    assert all(stage in _NONTERMINAL_STAGES for stage in stages), evidence


async def _admit_replay_and_assert_conflict(
    client: httpx.AsyncClient,
    server: ApiServer,
    config: ResearchInputE2eConfig,
) -> str:
    """Accept one run, then prove replay and collision need no second child."""
    query = build_three_form_query(config)
    headers = {
        **auth_header(server),
        "Idempotency-Key": f"research-input-e2e-{uuid.uuid4()}",
    }
    request = {"arguments": {"user_query": query}}

    accepted_body = _json_object(
        await client.post(
            "/v1/agents/research/runs", headers=headers, json=request
        ),
        expected_status=202,
    )
    assert_redacted_evidence(accepted_body, config)
    run_id = accepted_body.get("run_id")
    assert isinstance(run_id, str) and run_id, "admission returned no run id"

    replay_body = _json_object(
        await client.post(
            "/v1/agents/research/runs", headers=headers, json=request
        ),
        expected_status=200,
    )
    assert_redacted_evidence(replay_body, config)
    assert replay_body.get("run_id") == run_id, "replay created another run"

    conflict_body = _json_object(
        await client.post(
            "/v1/agents/research/runs",
            headers=headers,
            json={"arguments": {"user_query": f"different request\n{query}"}},
        ),
        expected_status=409,
    )
    assert_redacted_evidence(conflict_body, config)
    error = conflict_body.get("error")
    assert isinstance(error, dict) and error.get("code") == (
        "research_idempotency_conflict"
    ), "same-key conflict returned the wrong stable code"
    return run_id


async def _await_research_terminal(
    client: httpx.AsyncClient,
    server: ApiServer,
    run_id: str,
    timeout_seconds: float,
    config: ResearchInputE2eConfig,
) -> tuple[str, tuple[str, ...]]:
    """Poll public lifecycle state without retaining or printing raw bodies."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    stages: list[str] = []
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            f"/v1/runs/{run_id}", headers=auth_header(server)
        )
        assert response.status_code == 200, "Research status lookup failed"
        body = _json_object(response)
        assert_redacted_evidence(body, config)
        status = body.get("status")
        assert isinstance(status, str), "Research status was absent"
        normalized_status = status.lower()
        stage = body.get("stage")
        if normalized_status in _TERMINAL_STATUSES:
            assert stage is None, "terminal Research record retained a stage"
            return normalized_status, tuple(stages)
        assert (
            isinstance(stage, str) and stage in _NONTERMINAL_STAGES
        ), "nonterminal Research record had an invalid stage"
        if not stages or stages[-1] != stage:
            stages.append(stage)
        await asyncio.sleep(5.0)
    raise AssertionError(
        "bounded Research e2e run did not reach terminal state"
    )


def _json_object(
    response: httpx.Response, *, expected_status: int | None = None
) -> dict[str, Any]:
    """Decode an API object without including its potentially private body."""
    if expected_status is not None:
        assert (
            response.status_code == expected_status
        ), "Research API response had an unexpected status"
    body = response.json()
    assert isinstance(body, dict), "Research API response was not an object"
    return body
