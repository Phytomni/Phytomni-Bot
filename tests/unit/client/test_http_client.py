# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the key-safe asynchronous HTTP run client."""

from __future__ import annotations

import json as jsonlib
from typing import Any, Self

import httpx
import pytest

from mcp_client_phytomni.http_client import (
    HttpClientError,
    PhytomniHttpClient,
    RunProtocolError,
)
from mcp_server_phytomni.contracts.deep_genome import DEEP_GENOME_REPORT_FIELDS

pytestmark = pytest.mark.unit


def _json_response(status: int, payload: Any) -> httpx.Response:
    """Build a JSON response for the scripted transport."""
    return httpx.Response(status, json=payload)


def _scripted_client(
    responses: list[httpx.Response],
) -> tuple[Any, list[httpx.Request]]:
    """Build an async client that returns responses in order."""
    requests: list[httpx.Request] = []

    class _ScriptedClient:
        """Minimal injected transport that avoids real network calls."""

        async def __aenter__(self) -> Self:
            """Return this scripted transport."""
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: Any,
        ) -> None:
            """Leave the scripted transport open for assertions."""
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str],
            json: Any,
        ) -> httpx.Response:
            """Return the next scripted response and capture its request."""
            request = httpx.Request(method, url, headers=headers, json=json)
            requests.append(request)
            if not responses:
                raise AssertionError("scripted response queue is empty")
            return responses.pop(0)

        async def aclose(self) -> None:
            """Match the close method of an injected HTTP client."""
            return None

    return _ScriptedClient(), requests


async def test_http_client_submits_and_reads_run() -> None:
    """Submit a run and read its public degraded snapshot."""
    client, requests = _scripted_client(
        [
            _json_response(
                202,
                {"id": "run-1", "task_ids": ["task-1"], "status": "running"},
            ),
            _json_response(
                200,
                {
                    "run_id": "run-1",
                    "status": "running",
                    "result": {
                        "intermediate_report": "# partial",
                        "report_stage": "intermediate",
                        "report_completeness": "partial",
                        "report_revision": 2,
                        "progress": {
                            "total": 12,
                            "running": 3,
                            "submitted_task_id": "must-drop",
                        },
                        "degraded": True,
                        "degraded_reason": (
                            "1 of 12 optional analyses unavailable"
                        ),
                        "failures": [
                            {
                                "work_item_key": "protein_design",
                                "status": "failed",
                                "message": "analysis task failed",
                                "traceback": "must-drop",
                            }
                        ],
                    },
                },
            ),
        ]
    )
    async with (
        client,
        PhytomniHttpClient(
            "https://bot.invalid/", "top-secret", client=client
        ) as api,
    ):
        submitted = await api.submit("deep_genome", {"gene_id": "x"})
        snapshot = await api.get_run("run-1")

    assert submitted.run_id == "run-1"
    assert submitted.task_ids == ("task-1",)
    assert snapshot.status == "running"
    assert tuple(snapshot.__dict__) == (
        "run_id",
        "status",
        "answer",
        *DEEP_GENOME_REPORT_FIELDS,
    )
    assert snapshot.intermediate_report == "# partial"
    assert snapshot.report_revision == 2
    assert snapshot.progress == {"total": 12, "running": 3}
    assert snapshot.degraded_reason == (
        "1 of 12 optional analyses unavailable"
    )
    assert snapshot.failures == (
        {
            "work_item_key": "protein_design",
            "status": "failed",
            "message": "analysis task failed",
        },
    )
    assert not hasattr(snapshot, "task_ids")
    assert requests[0].url.path == "/v1/agents/deep_genome/runs"
    assert requests[0].headers["authorization"] == "Bearer top-secret"
    assert jsonlib.loads(requests[0].content) == {
        "arguments": {"gene_id": "x"}
    }
    assert requests[1].url.path == "/v1/runs/run-1"


async def test_http_client_errors_never_expose_key_or_body() -> None:
    """HTTP status failures use fixed text without upstream response data."""
    client, _ = _scripted_client(
        [
            httpx.Response(
                503,
                content=b"postgresql://u:p@host/internal-secret",
            )
        ]
    )
    async with client:
        api = PhytomniHttpClient(
            "https://bot.invalid", "top-secret", client=client
        )
        with pytest.raises(HttpClientError) as excinfo:
            await api.get_run("run-1")

    message = str(excinfo.value)
    assert "top-secret" not in message
    assert "postgresql://" not in message
    assert "internal-secret" not in message


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502, 503])
async def test_http_client_rejects_unexpected_status(status: int) -> None:
    """Unexpected HTTP statuses map to a safe client error."""
    client, _ = _scripted_client([_json_response(status, {"secret": "body"})])
    async with client:
        api = PhytomniHttpClient("https://bot.invalid", "key", client=client)
        with pytest.raises(HttpClientError, match=f"status {status}"):
            await api.get_run("run-1")


async def test_http_client_rejects_invalid_json_without_body() -> None:
    """Malformed JSON is a protocol error without echoing response text."""
    client, _ = _scripted_client(
        [httpx.Response(200, content=b"not-json-secret")]
    )
    async with client:
        api = PhytomniHttpClient("https://bot.invalid", "key", client=client)
        with pytest.raises(RunProtocolError) as excinfo:
            await api.get_run("run-1")

    assert "not-json-secret" not in str(excinfo.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("top-secret timeout"),
        httpx.ConnectError("postgresql://internal-secret"),
    ],
)
async def test_http_client_maps_transport_errors_to_fixed_text(
    error: httpx.RequestError,
) -> None:
    """Transport failures do not leak exception text."""
    client, _ = _scripted_client([])

    async def fail_request(
        _method: str,
        _url: str,
        *,
        headers: dict[str, str],
        json: Any,
    ) -> httpx.Response:
        del headers, json
        raise error

    client.request = fail_request
    async with client:
        api = PhytomniHttpClient(
            "https://bot.invalid", "top-secret", client=client
        )
        with pytest.raises(HttpClientError) as excinfo:
            await api.get_run("run-1")

    assert "top-secret" not in str(excinfo.value)
    assert "postgresql://" not in str(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"task_ids": ["task-1"]},
        {"id": "", "task_ids": []},
        {"id": 42, "task_ids": []},
    ],
)
async def test_http_client_rejects_malformed_submit_shape(
    payload: dict[str, Any],
) -> None:
    """Submit responses require a nonblank string run id."""
    client, _ = _scripted_client([_json_response(202, payload)])
    async with client:
        api = PhytomniHttpClient("https://bot.invalid", "key", client=client)
        with pytest.raises(RunProtocolError):
            await api.submit("deep_genome", {})


@pytest.mark.parametrize(
    "payload",
    [
        {"run_id": "run-1"},
        {"run_id": "run-1", "status": "queued"},
        {"run_id": "run-1", "status": "succeeded", "result": []},
    ],
)
async def test_http_client_rejects_malformed_run_shape(
    payload: dict[str, Any],
) -> None:
    """Run responses require a supported status and no child-id surface."""
    client, _ = _scripted_client([_json_response(200, payload)])
    async with client:
        api = PhytomniHttpClient("https://bot.invalid", "key", client=client)
        with pytest.raises(RunProtocolError):
            await api.get_run("run-1")


async def test_http_client_percent_encodes_path_and_repr_is_key_safe() -> None:
    """Path ids are encoded and the client representation omits the key."""
    client, requests = _scripted_client(
        [
            _json_response(
                200,
                {"run_id": "run/a", "status": "failed", "result": {}},
            )
        ]
    )
    async with client:
        api = PhytomniHttpClient(
            "https://bot.invalid/api?token=top-secret",
            "top-secret",
            client=client,
        )
        snapshot = await api.get_run("run/a")

    assert snapshot.run_id == "run/a"
    assert requests[0].url.raw_path == b"/api/v1/runs/run%2Fa"
    assert "top-secret" not in repr(api)
    assert "token=" not in repr(api)
