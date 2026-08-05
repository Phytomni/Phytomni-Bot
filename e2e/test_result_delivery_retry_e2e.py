# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e: manual archive retry never resubmits Analyst science."""

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
from .helpers.assertions import assert_remote_run_terminal_payload
from .helpers.polling import poll_http_run_to_terminal

pytestmark = pytest.mark.live


@pytest.fixture(scope="session", name="retry_api_server")
def retry_api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the guarded fail-three-then-delegate API process."""
    with boot_phytomni_api(
        tmp_path_factory,
        app_module="e2e.helpers.fault_injected_api",
        environment={"PHYTOMNI_E2E_DELIVERY_FAIL_COUNT": "3"},
    ) as server:
        yield server


@pytest_asyncio.fixture(name="retry_api_client")
async def retry_api_client_fixture(
    retry_api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client bound to the fault-injected API process."""
    async with make_async_client(retry_api_server) as client:
        yield client


async def _fetch_run(
    client: httpx.AsyncClient, server: ApiServer, run_id: str
) -> dict[str, Any]:
    """Fetch one authenticated owner-scoped run record."""
    response = await client.get(
        f"/v1/runs/{run_id}", headers=auth_header(server)
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def _delivery(record: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical delivery block from one public run record."""
    result = record.get("result")
    assert isinstance(result, dict)
    execution = result.get("execution")
    assert isinstance(execution, dict)
    delivery = execution.get("delivery")
    assert isinstance(delivery, dict)
    return delivery


async def test_result_delivery_retry_e2e(
    retry_api_client: httpx.AsyncClient,
    retry_api_server: ApiServer,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """Three publish failures recover without a second Analyst submission."""
    submit = await retry_api_client.post(
        "/v1/agents/analyst/runs",
        json={"arguments": load_payload("analyst_agent.json")},
        headers=auth_header(retry_api_server),
    )
    assert submit.status_code in (200, 202)
    run_id = submit.json().get("id")
    assert isinstance(run_id, str) and run_id

    assert (
        await poll_http_run_to_terminal(
            retry_api_client,
            run_id,
            headers=auth_header(retry_api_server),
        )
    ).status == "succeeded"
    failed_record = await _fetch_run(
        retry_api_client, retry_api_server, run_id
    )
    failed_delivery = _delivery(failed_record)
    assert failed_delivery.get("status") == "failed"
    assert failed_delivery.get("error_code") == "archive_publish_failed"
    assert failed_delivery.get("retryable") is True
    before_task_ids = tuple(failed_record.get("task_ids") or ())
    assert before_task_ids
    digest = failed_delivery.get("inventory_digest")
    assert isinstance(digest, str) and digest.startswith("sha256:")
    failed_revision = failed_delivery.get("revision")
    assert isinstance(failed_revision, int)

    retry = await retry_api_client.post(
        f"/v1/runs/{run_id}/delivery/retry",
        headers=auth_header(retry_api_server),
    )
    assert retry.status_code == 200
    pending = retry.json()
    assert pending.get("status") == "pending"
    assert pending.get("inventory_digest") == digest
    assert pending.get("revision") > failed_revision

    ready_terminal = await poll_http_run_to_terminal(
        retry_api_client,
        run_id,
        headers=auth_header(retry_api_server),
    )
    assert ready_terminal.status == "succeeded"
    assert_remote_run_terminal_payload(ready_terminal.result, agent="analyst")
    ready_record = await _fetch_run(retry_api_client, retry_api_server, run_id)
    ready_delivery = _delivery(ready_record)
    assert ready_delivery.get("status") == "ready"
    assert ready_delivery.get("inventory_digest") == digest
    assert ready_delivery.get("revision") == pending.get("revision")
    assert tuple(ready_record.get("task_ids") or ()) == before_task_ids
