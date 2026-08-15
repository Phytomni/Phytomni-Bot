# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Deterministic HTTP regression tests for Knowledge retrieval resilience."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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
)
from .helpers.knowledge_resilience_upstream import (
    KnowledgeScenarioMode,
    KnowledgeUpstream,
    boot_knowledge_resilience_upstream,
)
from .helpers.loopback_process import BoundedLogTail

pytestmark = pytest.mark.integration

_QUERY = "synthetic protein design question"
_CANCELLATION_QUERY = "synthetic cancellation question"
_CHAT_CACHE_FUNC = (
    "mcp_server_phytomni.agents.chat.service._run_chat_completion_cached"
)
_RETRIEVE_SCOPE_CACHE_FUNC = (
    "mcp_server_phytomni.agents.knowledge.retrieval._retrieve_scope_docs"
)
_HISTORY_MESSAGES = [
    {"role": "system", "content": "untrusted system text"},
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
    {"role": "user", "content": _QUERY},
]
_FIXED_FAILURE_MESSAGE = "Knowledge retrieval temporarily unavailable"
_SECRET_MARKERS = {
    "synthetic-provider-secret",
    "repo-b",
    "partial",
    "degraded",
}
_SUCCESS_FORBIDDEN_MARKERS = _SECRET_MARKERS | {"unavailable"}


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
    environment = {
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
        "DATABASE_URL": "sqlite:///synthetic-knowledge.sqlite",
        "ANALYSIS_URL": f"{upstream.base_url}/analysis",
        "REPO_ID": "synthetic-repo",
        "REPO_ID_DICT": json.dumps({"repo-a": 1, "repo-b": 1}),
        "WORKSPACE_ID": "synthetic-workspace",
        "SUBJECT_ID": "synthetic-subject",
        "OBS_SERVER": f"{upstream.base_url}/obs",
        "SCOPE": "doc",
        "MAX_RETRIES": "0",
        "TIMEOUT": "5",
    }
    for key in (
        "RETRIEVE_URL",
        "PHYTOMNI_RETRIEVE_URL",
        "RERANK_URL",
        "PHYTOMNI_RERANK_URL",
        "BASE_URL",
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


async def _list_runs(
    client: httpx.AsyncClient, server: ApiServer
) -> list[dict[str, Any]]:
    """Return the authenticated caller's public run projections."""
    response = await client.get("/v1/runs", headers=auth_header(server))
    assert response.status_code == 200, response.text
    data = response.json().get("data")
    assert isinstance(data, list)
    assert all(isinstance(item, dict) for item in data)
    return data


def _assert_no_success_run(runs: list[dict[str, Any]]) -> None:
    """Require every public run projection to remain redacted and non-success."""
    for run in runs:
        assert run["status"] == "failed"
        assert not run.get("result")
        serialized = json.dumps(run).lower()
        assert all(marker not in serialized for marker in _SECRET_MARKERS)


def _assert_redacted_bounded_logs(*tails: BoundedLogTail) -> None:
    """Require bounded diagnostic snapshots without synthetic secrets."""
    for tail in tails:
        snapshot = tail.snapshot()
        assert len(snapshot) <= tail.max_lines
        assert all(
            marker not in line.lower()
            for line in snapshot
            for marker in _SECRET_MARKERS
        )


async def _snapshot(upstream: KnowledgeUpstream) -> dict[str, Any]:
    """Read the upstream's synthetic-only snapshot."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{upstream.base_url}/snapshot")
    assert response.status_code == 200
    return response.json()


async def _wait_for_retrieve_streams(
    upstream: KnowledgeUpstream,
    *,
    closed: bool = False,
) -> dict[str, Any]:
    """Wait until both scripted retrieval bodies have the expected state."""
    for _ in range(100):
        snapshot = await _snapshot(upstream)
        streams = snapshot["retrieve"]["streams"]
        if streams["started"] == 2 and (
            not closed
            or (streams["active"] == 0 and streams["cancelled"] == 2)
        ):
            return snapshot
        await asyncio.sleep(0.05)
    pytest.fail("scripted retrieval streams did not reach the expected state")


async def _stream_then_reset(
    server: ApiServer,
) -> tuple[asyncio.StreamWriter, str]:
    """Open a raw HTTP stream and return after its RunStarted frame."""
    parsed = urlsplit(server.base_url)
    assert parsed.port is not None
    reader, writer = await asyncio.open_connection("127.0.0.1", parsed.port)
    body = json.dumps(
        {
            "model": "phyto-knowledge",
            "messages": [{"role": "user", "content": _CANCELLATION_QUERY}],
            "stream": True,
        }
    ).encode()
    request = b"\r\n".join(
        (
            b"POST /v1/chat/completions HTTP/1.1",
            f"Host: 127.0.0.1:{parsed.port}".encode(),
            f"Authorization: Bearer {server.api_key}".encode(),
            b"Content-Type: application/json",
            f"Content-Length: {len(body)}".encode(),
            b"Connection: close",
            b"",
            body,
        )
    )
    try:
        writer.write(request)
        await writer.drain()
        observed = bytearray()
        while b"RunStarted" not in observed:
            observed.extend(
                await asyncio.wait_for(reader.read(1024), timeout=5.0)
            )
            assert observed
        match = re.search(rb'"run_id":\s*"([^"]+)"', observed)
        assert match is not None
        return writer, match.group(1).decode()
    except BaseException:
        await _reset_stream(writer)
        raise


async def _reset_stream(writer: asyncio.StreamWriter) -> None:
    """Terminate the raw client connection with a deterministic TCP RST."""
    raw_socket = writer.get_extra_info("socket")
    assert raw_socket is not None
    raw_socket.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack("ii", 1, 0),
    )
    writer.transport.abort()
    with suppress(ConnectionResetError):
        await writer.wait_closed()


def _assert_safe_error(response: httpx.Response) -> None:
    """Assert the fixed public failure contains no upstream detail."""
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error"]["message"] == _FIXED_FAILURE_MESSAGE
    serialized = json.dumps(body).lower()
    assert all(marker not in serialized for marker in _SECRET_MARKERS)


def _visible_completion(body: dict[str, Any]) -> dict[str, Any]:
    """Return only the answer and formatted metadata visible to a caller."""
    return {
        "answer": body["choices"][0]["message"]["content"],
        "formatted": body.get("formatted", {}),
    }


def _assert_safe_success_completion(body: dict[str, Any]) -> None:
    """Require public answer and formatted metadata to omit failure details."""
    visible = json.dumps(_visible_completion(body)).lower()
    assert all(marker not in visible for marker in _SUCCESS_FORBIDDEN_MARKERS)


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
            _assert_safe_success_completion(first_body)

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
            assert _cache_counts(cache_path) == {
                _CHAT_CACHE_FUNC: 4,
                _RETRIEVE_SCOPE_CACHE_FUNC: 1,
            }

            second = await _post_chat(
                client, server, messages=list(_HISTORY_MESSAGES)
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert second_body["choices"][0]["message"]["content"].strip()
            _assert_safe_success_completion(second_body)

        second_snapshot = await _snapshot(upstream)
        assert second_snapshot["retrieve"]["calls"] == {
            "repo-a": 1,
            "repo-b": 2,
        }
        assert set(second_snapshot["retrieve"]["contents"]) == {_QUERY}
        assert _cache_counts(cache_path) == {
            _CHAT_CACHE_FUNC: 4,
            _RETRIEVE_SCOPE_CACHE_FUNC: 2,
        }


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
                client, server, messages=list(_HISTORY_MESSAGES)
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["choices"][0]["message"]["content"].strip()
        assert "<sup>1</sup>" in body["choices"][0]["message"]["content"]
        _assert_safe_success_completion(body)
        _assert_redacted_bounded_logs(server.log_tail, upstream.log_tail)
        snapshot = await _snapshot(upstream)
        assert snapshot["retrieve"]["calls"] == {"repo-a": 1, "repo-b": 1}
        assert set(snapshot["retrieve"]["contents"]) == {_QUERY}
        assert snapshot["provider"]["calls"] == 4
        assert ["system", "user", "assistant", "user"] in snapshot["provider"][
            "role_sequences"
        ]
        assert any(
            marker["current_query"]
            and marker["earlier_query"]
            and marker["earlier_answer"]
            for marker in snapshot["provider"]["history_markers"]
        )
        cache_counts = _cache_counts(cache_path)
        assert cache_counts == {
            _CHAT_CACHE_FUNC: 4,
            _RETRIEVE_SCOPE_CACHE_FUNC: 2,
        }


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
            runs = await _list_runs(client, server)
        _assert_safe_error(response)
        _assert_no_success_run(runs)
        _assert_redacted_bounded_logs(server.log_tail, upstream.log_tail)
        snapshot = await _snapshot(upstream)
        assert snapshot["provider"]["calls"] == 0
        assert _cache_counts(cache_path) == {}


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
            runs = await _list_runs(client, server)
        _assert_safe_error(response)
        _assert_no_success_run(runs)
        _assert_redacted_bounded_logs(server.log_tail, upstream.log_tail)
        snapshot = await _snapshot(upstream)
        assert snapshot["provider"]["calls"] == 0
        assert _cache_counts(cache_path) == {}


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
        _assert_safe_success_completion(body)
        _assert_redacted_bounded_logs(server.log_tail, upstream.log_tail)
        snapshot = await _snapshot(upstream)
        assert snapshot["rerank"]["calls"] == 2
        cache_counts = _cache_counts(cache_path)
        assert cache_counts == {
            _CHAT_CACHE_FUNC: 4,
            _RETRIEVE_SCOPE_CACHE_FUNC: 2,
        }


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


async def test_upstream_rejects_undeclared_route() -> None:
    """The loopback upstream exposes no fallback route surface."""
    with boot_knowledge_resilience_upstream("complete") as upstream:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{upstream.base_url}/undeclared")
    assert response.status_code == 404


async def test_stream_cancellation_closes_retrieval_without_completion(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A disconnected stream closes both retrieval bodies and cannot finish."""
    with _boot_case("complete", tmp_path, tmp_path_factory) as (
        upstream,
        server,
        cache_path,
    ):
        writer, run_id = await _stream_then_reset(server)
        try:
            snapshot = await _wait_for_retrieve_streams(upstream)
            assert snapshot["retrieve"]["streams"] == {
                "active": 2,
                "cancelled": 0,
                "started": 2,
            }
            await _reset_stream(writer)
            snapshot = await _wait_for_retrieve_streams(upstream, closed=True)
            async with make_async_client(server) as client:
                run = await client.get(
                    f"/v1/runs/{run_id}", headers=auth_header(server)
                )
        finally:
            if not writer.is_closing():
                await _reset_stream(writer)

        assert run.status_code == 200, run.text
        assert snapshot["retrieve"]["streams"] == {
            "active": 0,
            "cancelled": 2,
            "started": 2,
        }
        assert snapshot["provider"]["calls"] == 0
        assert _cache_counts(cache_path) == {}
        run_body = run.json()
        assert run_body["status"] == "failed"
        assert run_body["status"] not in {"succeeded", "completed"}
        assert run_body.get("error") != _FIXED_FAILURE_MESSAGE
        result = run_body.get("result")
        assert isinstance(result, dict)
        formatted = result.get("formatted")
        assert isinstance(formatted, dict)
        assert formatted.get("answer") == ""
        assert formatted.get("references") == []
        assert formatted.get("follow_up_questions") == []
        assert (
            _FIXED_FAILURE_MESSAGE.lower() not in json.dumps(run_body).lower()
        )
        _assert_redacted_bounded_logs(server.log_tail, upstream.log_tail)
