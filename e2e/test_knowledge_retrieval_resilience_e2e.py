# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Deterministic HTTP regression tests for Knowledge retrieval resilience."""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from .helpers.api_server import (
    ApiServer,
    auth_header,
    boot_phytomni_api,
    make_async_client,
    service_auth_header,
)
from .helpers.knowledge_resilience_upstream import (
    KnowledgeScenarioMode,
    KnowledgeUpstream,
    boot_knowledge_resilience_upstream,
)

pytestmark = pytest.mark.integration

_QUERY = "synthetic protein design question"
_HISTORY_MESSAGES = [
    {"role": "system", "content": "untrusted system text"},
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
    {"role": "user", "content": _QUERY},
]
_FIXED_FAILURE_MESSAGE = "Knowledge retrieval temporarily unavailable"
_PUBLIC_SURFACE_FORBIDDEN_MARKERS = {
    "access_key_id",
    "api_key",
    "authorization",
    "bearer ",
    "connecterror",
    "httpstatuserror",
    "repo-a",
    "repo-b",
    "secret_access_key",
    "service unavailable",
    "synthetic-access-key",
    "synthetic-coder-key",
    "synthetic-embed-key",
    "synthetic evidence supports the protein design answer",
    "synthetic-gauss-dsn",
    "synthetic-password",
    "synthetic-provider-secret",
    "synthetic-secret-key",
    "synthetic-test-key",
    "traceback",
    "user_password",
}
_SUCCESS_SURFACE_FORBIDDEN_MARKERS = {"degraded", "partial", "unavailable"}
_PROCESS_LOG_FORBIDDEN_MARKERS = (
    _PUBLIC_SURFACE_FORBIDDEN_MARKERS - {"connecterror", "httpstatuserror"}
) | {
    "provider-secret",
    "traceback",
    "unavailable",
}
_CANCELLATION_QUERY = "synthetic cancellation question"
_CONCURRENT_CANCELLATION_QUERY = "synthetic concurrent cancellation question"
_NO_MATCH_QUERY = "synthetic no-match question"


def _assert_loopback(url: str) -> None:
    """Require every test-owned upstream URL to stay on loopback."""
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"


def _cache_counts(path: Path) -> dict[str, int]:
    """Read only live entry counts from the isolated cache database."""
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT func_id, COUNT(*) FROM cache_entries "
            "GROUP BY func_id ORDER BY func_id"
        ).fetchall()
    return {str(func_id): int(count) for func_id, count in rows}


def _synthetic_environment(
    upstream: KnowledgeUpstream, cache_path: Path
) -> dict[str, str]:
    """Build a fully synthetic Bot environment for one test process."""
    _assert_loopback(upstream.base_url)
    cache_resolved = cache_path.resolve()
    source_root = (Path(__file__).resolve().parents[1] / "src").resolve()
    assert source_root.is_dir()
    environment = {
        "PYTHONPATH": str(source_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PHYTOMNI_TESTING": "1",
        "PHYTOMNI_RELAY_MODE": "0",
        "RELAY_MODE": "0",
        "PHYTOMNI_CACHE_DB": str(cache_resolved),
        "RETRIEVE_URL": f"{upstream.base_url}/retrieve",
        "PHYTOMNI_RETRIEVE_URL": f"{upstream.base_url}/retrieve",
        "RERANK_URL": f"{upstream.base_url}/rerank",
        "PHYTOMNI_RERANK_URL": f"{upstream.base_url}/rerank",
        "BASE_URL": f"{upstream.base_url}/v1",
        "MODEL_ID": "knowledge-resilience-model",
        "API_KEY": "synthetic-test-key",
        "CODER_URL": f"{upstream.base_url}/v1",
        "CODER_MODEL": "synthetic-coder",
        "CODER_API_KEY": "synthetic-coder-key",
        "EMBED_URL": f"{upstream.base_url}/v1",
        "EMBED_MODEL": "synthetic-embed",
        "EMBED_API_KEY": "synthetic-embed-key",
        "GAUSS_DSN": "synthetic-gauss-dsn",
        "DOMAIN_NAME": "127.0.0.1",
        "USER_NAME": "synthetic-user",
        "USER_PASSWORD": "synthetic-password",
        "ACCESS_KEY_ID": "synthetic-access-key",
        "SECRET_ACCESS_KEY": "synthetic-secret-key",
        "TOKEN_URL": f"{upstream.base_url}/token",
        "DATABASE_URL": f"{upstream.base_url}/database",
        "ANALYSIS_URL": f"{upstream.base_url}/analysis",
        "SPA_FAQ_URL": f"{upstream.base_url}/repos/{{repo_id}}/faqs",
        "REPO_ID": "synthetic-repo",
        "REPO_ID_DICT": json.dumps({"repo-a": 1, "repo-b": 1}),
        "WORKSPACE_ID": "synthetic-workspace",
        "SUBJECT_ID": "synthetic-subject",
        "DATA_REPO_ID": "synthetic-data-repo",
        "TOOL_REPO_ID": "synthetic-tool-repo",
        "PROTOCOL_REPO_ID": "synthetic-protocol-repo",
        "SPA_REPO_ID": "synthetic-spa-repo",
        "APP_ID": json.dumps(
            {
                "small": "00000000-0000-0000-0000-000000000001",
                "medium": "00000000-0000-0000-0000-000000000002",
                "large": "00000000-0000-0000-0000-000000000003",
            }
        ),
        "OBS_SERVER": f"{upstream.base_url}/obs",
        "SCOPE": "doc",
        "MAX_RETRIES": "0",
        "TIMEOUT": "30",
    }
    pool_names = (
        "ANALYSIS_CONTROL",
        "ANALYSIS_STATUS",
        "BI",
        "IAM",
        "INTEROP",
        "LLM",
        "NL2SQL",
        "OBS",
        "RELAY_CONTROL",
        "RERANK",
        "RETRIEVAL",
        "SPA_FAQ",
    )
    for pool_name in pool_names:
        environment[f"OUTBOUND_{pool_name}_CONCURRENCY"] = "0"
    environment["OUTBOUND_POOL_WAIT_WARN_SECONDS"] = "1"
    for key in (
        "RETRIEVE_URL",
        "PHYTOMNI_RETRIEVE_URL",
        "RERANK_URL",
        "PHYTOMNI_RERANK_URL",
        "BASE_URL",
        "CODER_URL",
        "EMBED_URL",
        "TOKEN_URL",
        "DATABASE_URL",
        "ANALYSIS_URL",
        "SPA_FAQ_URL",
        "OBS_SERVER",
    ):
        _assert_loopback(environment[key])
    assert cache_resolved.is_relative_to(cache_path.parent.resolve())
    return environment


@contextmanager
def _boot_case(
    mode: KnowledgeScenarioMode,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[KnowledgeUpstream, ApiServer, Path]]:
    """Boot a fresh upstream and authenticated Bot API for one case."""
    cache_path = (tmp_path / "cache.sqlite").resolve()
    with boot_knowledge_resilience_upstream(mode) as upstream:
        environment = _synthetic_environment(upstream, cache_path)
        with boot_phytomni_api(
            tmp_path_factory, environment=environment
        ) as server:
            yield upstream, server, cache_path


async def _post_chat(
    client: httpx.AsyncClient,
    server: ApiServer,
    *,
    messages: list[dict[str, str]],
) -> httpx.Response:
    """Send one authenticated Knowledge completion over real HTTP."""
    return await client.post(
        "/v1/chat/completions",
        json={"model": "phyto-knowledge", "messages": messages},
        headers=auth_header(server),
    )


async def _snapshot(upstream: KnowledgeUpstream) -> dict[str, Any]:
    """Read the upstream's synthetic-only snapshot."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{upstream.base_url}/snapshot")
    assert response.status_code == 200
    return response.json()


def _assert_public_surface_safe(payload: Any) -> None:
    """Reject repository, source, credential, and exception disclosure."""
    serialized = json.dumps(payload, sort_keys=True).lower()
    assert all(
        marker not in serialized
        for marker in _PUBLIC_SURFACE_FORBIDDEN_MARKERS
    )


def _assert_success_surface_safe(payload: Any) -> None:
    """Reject every partial or unavailable marker from a normal answer."""
    _assert_public_surface_safe(payload)
    serialized = json.dumps(payload, sort_keys=True).lower()
    assert all(
        marker not in serialized
        for marker in _SUCCESS_SURFACE_FORBIDDEN_MARKERS
    )


def _assert_process_logs_safe(
    upstream: KnowledgeUpstream,
    server: ApiServer,
) -> None:
    """Inspect bounded child tails without surfacing their raw contents."""
    upstream_lines = upstream.log_tail.snapshot()
    server_lines = server.log_tail.snapshot()
    assert len(upstream_lines) <= 200
    assert len(server_lines) <= 500
    serialized = "\n".join((*upstream_lines, *server_lines)).lower()
    for marker in _PROCESS_LOG_FORBIDDEN_MARKERS:
        assert marker not in serialized


def _assert_safe_error(response: httpx.Response) -> None:
    """Assert the fixed public failure contains no upstream detail."""
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error"]["message"] == _FIXED_FAILURE_MESSAGE
    _assert_public_surface_safe(body)


async def _assert_terminal_run_surfaces(
    client: httpx.AsyncClient,
    server: ApiServer,
    run_id: str,
    *,
    status: str,
) -> dict[str, Any]:
    """Assert one owner-scoped terminal run and its empty task-log view."""
    detail = await client.get(
        f"/v1/runs/{run_id}", headers=auth_header(server)
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["run_id"] == run_id
    assert body["agent"] == "knowledge"
    assert body["status"] == status
    assert body["task_ids"] == []
    _assert_public_surface_safe(body)

    logs = await client.get(
        f"/v1/runs/{run_id}/logs", headers=auth_header(server)
    )
    assert logs.status_code == 200, logs.text
    assert logs.json() == {
        "run_id": run_id,
        "task_ids": [],
        "task_logs": [],
    }
    _assert_public_surface_safe(logs.json())
    return body


async def _assert_foreign_owner_cannot_read_run(
    client: httpx.AsyncClient, server: ApiServer, run_id: str
) -> None:
    """Mint a second owner and prove both run surfaces fail closed."""
    created = await client.post(
        "/v1/api-keys",
        json={"user_id": "knowledge-e2e-foreign", "name": "foreign-owner"},
        headers=service_auth_header(server),
    )
    assert created.status_code == 201, created.text
    foreign = {"Authorization": f"Bearer {created.json()['api_key']}"}
    detail = await client.get(f"/v1/runs/{run_id}", headers=foreign)
    logs = await client.get(f"/v1/runs/{run_id}/logs", headers=foreign)
    assert detail.status_code == logs.status_code == 404
    _assert_public_surface_safe(detail.json())
    _assert_public_surface_safe(logs.json())


async def _assert_no_succeeded_run(
    client: httpx.AsyncClient, server: ApiServer
) -> list[dict[str, Any]]:
    """Prove failed retrieval never becomes a successful run projection."""
    response = await client.get("/v1/runs", headers=auth_header(server))
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_public_surface_safe(body)
    rows = body["data"]
    assert all(row["status"] not in {"succeeded", "completed"} for row in rows)
    return rows


async def _wait_for_snapshot(
    upstream: KnowledgeUpstream,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    budget_seconds: float = 5.0,
) -> dict[str, Any]:
    """Wait for one bounded upstream observation."""
    latest: dict[str, Any] = {}
    try:
        async with asyncio.timeout(budget_seconds):
            while True:
                latest = await _snapshot(upstream)
                if predicate(latest):
                    return latest
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"upstream observation timed out: {latest}")
    raise AssertionError("unreachable")


async def _wait_for_failed_run(
    client: httpx.AsyncClient,
    server: ApiServer,
    *,
    budget_seconds: float = 5.0,
) -> dict[str, Any]:
    """Wait until one disconnected stream has a failed terminal projection."""
    latest: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(budget_seconds):
            while True:
                latest = await _assert_no_succeeded_run(client, server)
                if len(latest) == 1 and latest[0].get("status") == "failed":
                    return latest[0]
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"failed run projection timed out: {latest}")
    raise AssertionError("unreachable")


async def _wait_for_run_status(
    client: httpx.AsyncClient,
    server: ApiServer,
    run_id: str,
    *,
    status: str,
    budget_seconds: float = 5.0,
) -> dict[str, Any]:
    """Wait for one known run without making assumptions about sibling rows."""
    latest: dict[str, Any] = {}
    try:
        async with asyncio.timeout(budget_seconds):
            while True:
                response = await client.get(
                    f"/v1/runs/{run_id}",
                    headers=auth_header(server),
                )
                assert response.status_code == 200, response.text
                latest = response.json()
                _assert_public_surface_safe(latest)
                if latest.get("status") == status:
                    return latest
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(
            "run status timed out: "
            f"run_id={run_id} observed_status={latest.get('status')}"
        )
    raise AssertionError("unreachable")


def _visible_completion(body: dict[str, Any]) -> dict[str, Any]:
    """Return only the answer and formatted metadata visible to a caller."""
    return {
        "answer": body["choices"][0]["message"]["content"],
        "formatted": body.get("formatted", {}),
    }


async def test_partial_then_complete_retries_only_failed_repository(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A reliable first answer survives one failed leaf and its retry."""
    with _boot_case("partial_then_complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            first = await _post_chat(
                client, server, messages=list(_HISTORY_MESSAGES)
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert first_body["choices"][0]["message"]["content"].strip()
            assert (
                "<sup>1</sup>"
                in first_body["choices"][0]["message"]["content"]
            )
            _assert_success_surface_safe(_visible_completion(first_body))
            first_run = await _assert_terminal_run_surfaces(
                client,
                server,
                first_body["run_id"],
                status="succeeded",
            )
            assert "retrieval_outcome" not in json.dumps(first_run)

            first_snapshot = await _snapshot(upstream)
            assert first_snapshot["retrieve"]["calls"] == {
                "repo-a": 1,
                "repo-b": 1,
            }
            assert set(first_snapshot["retrieve"]["contents"]) == {_QUERY}
            assert ["system", "user", "assistant", "user"] in first_snapshot[
                "provider"
            ]["role_sequences"]
            assert any(
                marker["current_query"]
                and marker["earlier_query"]
                and marker["earlier_answer"]
                for marker in first_snapshot["provider"]["history_markers"]
            )

            second = await _post_chat(
                client, server, messages=list(_HISTORY_MESSAGES)
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert second_body["choices"][0]["message"]["content"].strip()
            await _assert_terminal_run_surfaces(
                client,
                server,
                second_body["run_id"],
                status="succeeded",
            )

        second_snapshot = await _snapshot(upstream)
        assert second_snapshot["retrieve"]["calls"] == {
            "repo-a": 1,
            "repo-b": 2,
        }
        assert set(second_snapshot["retrieve"]["contents"]) == {_QUERY}
        cache_counts = _cache_counts(cache_path)
        assert cache_counts
        assert not any("multi_retrieve" in func_id for func_id in cache_counts)
    _assert_process_logs_safe(upstream, server)


async def test_complete_control_preserves_citations_and_cache_policy(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A complete first request keeps normal citation and primitive caching."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client, server, messages=[{"role": "user", "content": _QUERY}]
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["choices"][0]["message"]["content"].strip()
            assert "<sup>1</sup>" in body["choices"][0]["message"]["content"]
            await _assert_terminal_run_surfaces(
                client, server, body["run_id"], status="succeeded"
            )
            await _assert_foreign_owner_cannot_read_run(
                client, server, body["run_id"]
            )
        snapshot = await _snapshot(upstream)
        assert snapshot["retrieve"]["calls"] == {"repo-a": 1, "repo-b": 1}
        assert snapshot["provider"]["calls"] == 4
        cache_counts = _cache_counts(cache_path)
        assert cache_counts
        assert not any("multi_retrieve" in func_id for func_id in cache_counts)
    _assert_process_logs_safe(upstream, server)


@pytest.mark.parametrize(
    ("query", "label"),
    [
        ("synthetic all failed question", "all repositories failed"),
        ("synthetic valid-empty question", "valid empty plus failure"),
    ],
)
async def test_failed_knowledge_evidence_is_not_projected_as_empty_success(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    query: str,
    label: str,
) -> None:
    """All-unreliable evidence returns one fixed 500 for each failure shape."""
    del label
    with _boot_case("all_failed", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client,
                server,
                messages=[{"role": "user", "content": query}],
            )
            _assert_safe_error(response)
            assert await _assert_no_succeeded_run(client, server) == []
        snapshot = await _snapshot(upstream)
        assert snapshot["provider"]["calls"] == 0
        assert _cache_counts(cache_path) == {}
    _assert_process_logs_safe(upstream, server)


async def test_malformed_retrieve_payload_fails_safely(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A malformed retrieve payload cannot enter the cache or answer path."""
    with _boot_case("malformed", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client,
                server,
                messages=[
                    {
                        "role": "user",
                        "content": "synthetic malformed-retrieve question",
                    }
                ],
            )
            _assert_safe_error(response)
            assert await _assert_no_succeeded_run(client, server) == []
        snapshot = await _snapshot(upstream)
        assert snapshot["provider"]["calls"] == 0
        assert _cache_counts(cache_path) == {}
    _assert_process_logs_safe(upstream, server)


async def test_malformed_rerank_keeps_stable_fallback_out_of_merged_cache(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Malformed rerank output keeps reliable source docs as a partial miss."""
    with _boot_case("malformed", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client,
                server,
                messages=[
                    {
                        "role": "user",
                        "content": "synthetic malformed-rerank question",
                    }
                ],
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["choices"][0]["message"]["content"].strip()
            _assert_success_surface_safe(_visible_completion(body))
            projected = await _assert_terminal_run_surfaces(
                client, server, body["run_id"], status="succeeded"
            )
            assert "retrieval_outcome" not in json.dumps(projected)
        snapshot = await _snapshot(upstream)
        assert snapshot["rerank"]["calls"] == 2
        cache_counts = _cache_counts(cache_path)
        assert cache_counts
        assert not any(
            "_retrieve_cached" in func_id for func_id in cache_counts
        )
    _assert_process_logs_safe(upstream, server)


async def test_trailing_assistant_is_rejected_before_retrieval(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A malformed conversation cannot reach the Knowledge upstream."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        _cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client,
                server,
                messages=[
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "answer"},
                ],
            )
        assert response.status_code == 400, response.text
        snapshot = await _snapshot(upstream)
        assert snapshot["retrieve"]["calls"] == {}
        assert snapshot["provider"]["calls"] == 0
    _assert_process_logs_safe(upstream, server)


async def test_no_match_is_a_success_without_fake_references(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A reliable empty search remains a typed no-match, not a failure."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        async with make_async_client(server) as client:
            response = await _post_chat(
                client,
                server,
                messages=[{"role": "user", "content": _NO_MATCH_QUERY}],
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["choices"][0]["message"]["content"].strip()
            assert body.get("formatted", {}).get("references", []) == []
            visible = _visible_completion(body)
            serialized = json.dumps(visible).lower()
            assert "no_match" not in serialized
            _assert_success_surface_safe(visible)
            projected = await _assert_terminal_run_surfaces(
                client, server, body["run_id"], status="succeeded"
            )
            assert "retrieval_outcome" not in json.dumps(projected)
        snapshot = await _snapshot(upstream)
        assert snapshot["retrieve"]["calls"] == {"repo-a": 1, "repo-b": 1}
        assert not any(
            "retrieve" in func_id for func_id in _cache_counts(cache_path)
        )
    _assert_process_logs_safe(upstream, server)


@dataclass
class LiveSseConnection:
    """Own one raw SSE socket until its deterministic TCP reset."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    run_id: str
    closed: bool = False

    async def reset(self) -> None:
        """Issue one RST and await local writer closure."""
        if self.closed:
            return
        self.closed = True
        try:
            raw_socket = self.writer.get_extra_info("socket")
            assert raw_socket is not None
            raw_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
        finally:
            self.writer.transport.abort()
            self.writer.close()
            with suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(self.writer.wait_closed(), timeout=1.0)


def _run_id_from_first_frame(frame: bytes) -> str:
    """Read only the synthetic RunStarted identity from one SSE frame."""
    data_lines = [
        line.removeprefix(b"data: ")
        for line in frame.splitlines()
        if line.startswith(b"data: ")
    ]
    assert len(data_lines) == 1
    payload = json.loads(data_lines[0])
    assert payload.get("type") == "RunStarted"
    run_id = payload.get("run_id")
    assert isinstance(run_id, str) and run_id
    return run_id


async def _open_live_sse_connection(
    server: ApiServer,
    *,
    query: str,
) -> LiveSseConnection:
    """Open one authenticated raw SSE socket with failure-safe ownership."""
    parsed = urlsplit(server.base_url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.open_connection(
            parsed.hostname, parsed.port
        )
        request_body = json.dumps(
            {
                "model": "phyto-knowledge",
                "messages": [{"role": "user", "content": query}],
                "stream": True,
            },
            separators=(",", ":"),
        ).encode()
        request_head = (
            "POST /v1/chat/completions HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            f"Authorization: Bearer {server.api_key}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(request_body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(request_head + request_body)
        await writer.drain()
        response_head = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=5.0
        )
        assert response_head.startswith(b"HTTP/1.1 200")
        first_frame = await asyncio.wait_for(
            reader.readuntil(b"\n\n"), timeout=5.0
        )
        return LiveSseConnection(
            reader=reader,
            writer=writer,
            run_id=_run_id_from_first_frame(first_frame),
        )
    except BaseException:
        if writer is not None:
            writer.close()
            with suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        raise


async def open_live_sse_connections(
    server: ApiServer,
    *,
    queries: tuple[str, str],
) -> tuple[LiveSseConnection, LiveSseConnection]:
    """Open two streams while retaining and resetting partial success."""
    connections: list[LiveSseConnection] = []

    async def acquire(query: str) -> LiveSseConnection:
        connection = await _open_live_sse_connection(server, query=query)
        connections.append(connection)
        return connection

    try:
        async with asyncio.TaskGroup() as group:
            first = group.create_task(acquire(queries[0]))
            second = group.create_task(acquire(queries[1]))
    except BaseException:
        await asyncio.gather(
            *(connection.reset() for connection in connections),
            return_exceptions=True,
        )
        raise
    return first.result(), second.result()


async def _reset_live_sse_connection(
    server: ApiServer,
    upstream: KnowledgeUpstream,
) -> str:
    """Open one real SSE socket and always reset it after retrieval starts."""
    connection = await _open_live_sse_connection(
        server,
        query=_CANCELLATION_QUERY,
    )
    try:
        started = await _wait_for_snapshot(
            upstream,
            lambda value: value.get("cancellation", {}).get("active") == 2,
        )
        assert started["cancellation"] == {
            "started": 2,
            "active": 2,
            "cancelled": 0,
            "completed": 0,
        }
        return connection.run_id
    finally:
        await connection.reset()


async def test_http_stream_cancellation_closes_retrieval_and_never_succeeds(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Resetting a real SSE connection closes both retrieval leaves."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        await _reset_live_sse_connection(server, upstream)

        async with make_async_client(server) as client:
            failed = await _wait_for_failed_run(client, server)
            run_id = failed["run_id"]
            assert _FIXED_FAILURE_MESSAGE not in json.dumps(failed)
            projected = await _assert_terminal_run_surfaces(
                client, server, run_id, status="failed"
            )
            assert _FIXED_FAILURE_MESSAGE not in json.dumps(projected)
            await _assert_foreign_owner_cannot_read_run(client, server, run_id)

        cancelled = await _wait_for_snapshot(
            upstream,
            lambda value: (
                value.get("cancellation", {}).get("active") == 0
                and value.get("cancellation", {}).get("cancelled") == 2
            ),
        )
        assert cancelled["cancellation"] == {
            "started": 2,
            "active": 0,
            "cancelled": 2,
            "completed": 0,
        }
        assert _cache_counts(cache_path) == {}
    _assert_process_logs_safe(upstream, server)


async def test_cancelling_one_of_two_live_http_streams_is_isolated(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A TCP reset for stream A leaves simultaneous stream B running."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        stream_a, stream_b = await open_live_sse_connections(
            server,
            queries=(
                _CANCELLATION_QUERY,
                _CONCURRENT_CANCELLATION_QUERY,
            ),
        )
        try:
            started = await _wait_for_snapshot(
                upstream,
                lambda value: (
                    value.get("cancellation", {}).get("active") == 4
                ),
            )
            assert started["cancellation"] == {
                "started": 4,
                "active": 4,
                "cancelled": 0,
                "completed": 0,
            }

            await stream_a.reset()
            isolated = await _wait_for_snapshot(
                upstream,
                lambda value: (
                    value.get("cancellation", {}).get("active") == 2
                    and value.get("cancellation", {}).get("cancelled") == 2
                ),
            )
            assert isolated["cancellation"] == {
                "started": 4,
                "active": 2,
                "cancelled": 2,
                "completed": 0,
            }

            async with make_async_client(server) as client:
                await _wait_for_run_status(
                    client,
                    server,
                    stream_a.run_id,
                    status="failed",
                )
                stream_b_detail = await client.get(
                    f"/v1/runs/{stream_b.run_id}",
                    headers=auth_header(server),
                )
                assert stream_b_detail.status_code == 200
                stream_b_body = stream_b_detail.json()
                _assert_public_surface_safe(stream_b_body)
                assert stream_b_body["status"] == "running"
                assert stream_b.writer.is_closing() is False
        finally:
            await asyncio.gather(stream_a.reset(), stream_b.reset())

        cancelled = await _wait_for_snapshot(
            upstream,
            lambda value: (
                value.get("cancellation", {}).get("active") == 0
                and value.get("cancellation", {}).get("cancelled") == 4
            ),
        )
        assert cancelled["cancellation"] == {
            "started": 4,
            "active": 0,
            "cancelled": 4,
            "completed": 0,
        }
        async with make_async_client(server) as client:
            await _wait_for_run_status(
                client,
                server,
                stream_b.run_id,
                status="failed",
            )
        assert _cache_counts(cache_path) == {}
    _assert_process_logs_safe(upstream, server)
