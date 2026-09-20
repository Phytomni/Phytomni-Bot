# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Real HTTP Agent handlers converge through the canonical Runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.all_agent_runtime_cases import REAL_HANDLER_FIXTURES

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)

pytestmark = pytest.mark.server


@pytest.mark.parametrize(
    "spec", PUBLIC_AGENT_CATALOG, ids=lambda item: item.slug
)
async def test_real_http_handler_reserves_drives_and_journals_once(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
    spec,
) -> None:
    """Fake the provider edge while exercising the production route."""
    dependency, arguments, expected = REAL_HANDLER_FIXTURES[spec.slug]
    calls: list[dict[str, object]] = []

    async def deterministic_provider(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return expected

    expected_status = (
        "running" if spec.lifecycle == "asynchronous" else "succeeded"
    )
    observed_calls: list[dict[str, object]] | list[object] = calls
    if spec.slug == "review":
        review_app = review_app_factory()
        monkeypatch.setattr(
            api_app_module, "_review_stream_app", lambda: review_app
        )
        monkeypatch.setattr(
            api_app_module,
            "_review_initial_state",
            lambda _args: {"seed": "review"},
        )
        observed_calls = review_app.calls
        expected_status = "waiting_input"
    else:
        monkeypatch.setattr(handlers, dependency, deterministic_provider)
    monkeypatch.setattr(
        handlers,
        "scratch_server_dir",
        lambda _config, scope: str(tmp_path / scope),
    )
    execution_id = f"turn-http-real-{spec.slug}"
    headers = {
        "Authorization": f"Bearer {issued_api_key}",
        "X-Phyto-Execution-Id": execution_id,
    }
    if spec.slug == "research":
        headers["Idempotency-Key"] = f"idempotency-{spec.slug}"

    response = await api_client.post(
        f"/v1/agents/{spec.slug}/runs",
        headers=headers,
        json={"arguments": arguments},
    )

    assert response.status_code in {200, 202}, response.text
    expected_side_effects = 0 if spec.slug == "research" else 1
    assert len(observed_calls) == expected_side_effects
    reservation = SQLiteExecutionReservationRepository(tasks_db_path).get(
        owner="u1", execution_id=execution_id
    )
    assert reservation.agent_slug == spec.slug
    assert reservation.driver == spec.driver
    assert reservation.status.value == expected_status
    page = SQLiteExecutionJournal(tasks_db_path).list_events(
        execution_id, owner="u1", limit=200
    )
    assert page is not None
    event_types = [event.type.value for event in page.items]
    assert event_types[0] == "execution.admitted"
    assert "todo.snapshot" not in event_types
    if spec.slug == "review":
        assert event_types.count("input.required") == 1, event_types
        assert "span.waiting_input" in event_types
    elif spec.slug == "research":
        # The dedicated Research route durably admits input; its worker owns
        # the later provider side effect and corresponding work-unit facts.
        assert event_types == [
            "execution.admitted",
            "execution.started",
            "span.started",
        ]
    else:
        assert event_types.count("work_unit.attempt_started") == 1
        assert event_types.count("work_unit.succeeded") == 1
    assert event_types.count("execution.succeeded") == (
        1 if expected_status == "succeeded" else 0
    )
