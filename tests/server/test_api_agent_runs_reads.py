# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Read-side projection contracts for native agent runs."""

from __future__ import annotations

import httpx
import pytest
from tests.support.sqlite import closed_sqlite_connection
from tests.support.terminal_results import (
    SensitiveTerminalResultSpec,
    public_partial_warning,
    public_report_projection,
    public_scientific_table_artifact,
    sensitive_terminal_result,
)

from mcp_server_phytomni.agents.shared.a2ui import validate_a2ui_surface
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


async def test_run_read_replaces_invalid_persisted_review_surface(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Read projection replaces malformed persisted Review A2UI safely."""
    run_id = "run-review-persisted-invalid"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={
                "private_result": {"provider_payload": "private"},
                "interrupt": {
                    "thread_id": "thread-review-safe",
                    "provider_trace": "private",
                    "draft": {
                        "draft": "review this result",
                        "a2ui": {"widget": "invalid"},
                        "provider_payload": {"secret": "private"},
                        "private_path": "/srv/private",
                    },
                },
            },
        ),
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listing = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert listing.status_code == 200
    for body in (response.json(), listing.json()["data"][0]):
        assert body["id"] == run_id
        assert body["run_id"] == run_id
        surface = body["result"]["interrupt"]["draft"]["a2ui"]
        validate_a2ui_surface(surface)
        assert surface["surface_id"] == f"{run_id}-review-confirm"
        assert body["result"]["interrupt"]["thread_id"] == (
            "thread-review-safe"
        )
        assert set(body["result"]) == {"interrupt", "status"}
        assert set(body["result"]["interrupt"]) == {"thread_id", "draft"}
        assert set(body["result"]["interrupt"]["draft"]) == {
            "summary",
            "a2ui",
        }


async def test_run_reads_publish_canonical_id_aliases(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Fetched and listed persisted rows expose byte-identical identities."""
    run_id = "run-canonical-read-id"
    result = empty_agent_result()
    result["formatted"]["answer"] = "complete"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result=result),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert fetched.json()["id"] == fetched.json()["run_id"] == run_id
    assert listed.status_code == 200
    row = listed.json()["data"][0]
    assert row["id"] == row["run_id"] == run_id


async def test_invalid_persisted_succeeded_state_maps_to_safe_error(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A malformed persisted result cannot be returned as valid data."""
    run_id = "run-invalid-persisted-succeeded"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={}),
    )
    with closed_sqlite_connection(tasks_db_path) as connection:
        connection.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = ?",
            ('"invalid"', run_id),
        )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    for response in (fetched, listed):
        assert response.status_code == 500
        error = response.json()["error"]
        assert isinstance(error["code"], str)
        assert error["stage"] in {"lifecycle", "projection"}


@pytest.mark.parametrize("status", ("succeeded", "failed"))
async def test_terminal_run_reads_project_nested_sensitive_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    status: str,
) -> None:
    """Terminal reads keep only the nested result projection allowlist."""
    run_id = f"run-terminal-without-formatted-{status}"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result=sensitive_terminal_result(
                SensitiveTerminalResultSpec(
                    answer="public terminal answer",
                    task_id="task-safe",
                    citation=("pm", "12345"),
                    table=(["gene"], [["AT1G01010"]]),
                    warning=("private_error", "provider exception"),
                )
            ),
            error="provider exception: private details",
        ),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    listed_row = next(
        row for row in listed.json()["data"] if row["id"] == run_id
    )
    for body in (fetched.json(), listed_row):
        assert body["id"] == body["run_id"] == run_id
        assert body["status"] == status
        assert body["result"]["formatted"] == {
            "answer": "public terminal answer",
            "follow_up_questions": ["next?"],
            "references": [
                {"file_id": "doc-1", "title": "Public title", "pm": "12345"}
            ],
            "tabular": {"headers": ["gene"], "rows": [["AT1G01010"]]},
            "metadata": {"original_query": "public query"},
        }
        assert body["result"]["execution"]["tracking"] == {"degraded": False}
        assert body["result"]["execution"]["warnings"] == [
            public_partial_warning()
        ]
        assert body["result"]["execution"]["tasks"] == [
            {"id": "task-safe", "accepted": True, "status": "succeeded"}
        ]
        assert body["result"]["execution"]["artifacts"] == [
            public_scientific_table_artifact()
        ]
        assert body["result"]["execution"]["output_dirs"] == [
            "/obs/public/result"
        ]
        assert body["result"]["execution"]["report"] == (
            public_report_projection()
        )
        assert body["result"]["execution"]["diagnostics"] == [
            {
                "code": "upstream_partial",
                "stage": "analysis",
                "retryable": False,
            }
        ]
        assert set(body["result"]) == {"formatted", "execution"}
        assert "provider_trace" not in body["result"]
        assert "raw" not in body["result"]
        assert "provider_payload" not in body["result"]["execution"]
        assert "provider_payload" not in str(body)
        assert "provider_trace" not in str(body)
        assert "private_error" not in str(body)
        if status == "failed":
            assert body["error"] == "run failed"
        else:
            assert "error" not in body


@pytest.mark.parametrize("status", ("succeeded", "failed"))
async def test_terminal_run_reads_default_missing_formatted_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    status: str,
) -> None:
    """Terminal reads default absent display data while retaining execution."""
    run_id = f"run-terminal-missing-formatted-{status}"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result={
                "execution": {
                    "tracking": {"degraded": False},
                    "tasks": [
                        {
                            "id": "task-retained",
                            "accepted": True,
                            "status": status,
                        }
                    ],
                    "artifacts": [
                        {
                            "role": "scientific_table",
                            "name": "retained.tsv",
                            "mime_type": "text/tab-separated-values",
                            "size_bytes": 8,
                        }
                    ],
                }
            },
            error="provider exception: private details",
        ),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    listed_row = next(
        row for row in listed.json()["data"] if row["id"] == run_id
    )
    for body in (fetched.json(), listed_row):
        assert body["result"]["formatted"] == empty_agent_result()["formatted"]
        assert body["result"]["execution"]["tasks"] == [
            {"id": "task-retained", "accepted": True, "status": status}
        ]
        assert body["result"]["execution"]["artifacts"] == [
            {
                "role": "scientific_table",
                "name": "retained.tsv",
                "mime_type": "text/tab-separated-values",
                "size_bytes": 8,
            }
        ]
        if status == "failed":
            assert body["error"] == "run failed"
        else:
            assert "error" not in body


async def test_persisted_running_projects_empty_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A persisted running row needs no terminal result to remain readable."""
    run_id = "run-persisted-running-no-result"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(status="running", result=None),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    for body in (fetched.json(), listed.json()["data"][0]):
        assert body["id"] == body["run_id"] == run_id
        assert body["status"] == "running"
        assert body["result"] == empty_agent_result()
