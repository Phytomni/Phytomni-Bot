# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared fixtures for HTTP server streaming tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, NamedTuple

import httpx
import pytest
from tests.support.http_fakes import parse_sse_frames

from mcp_server_phytomni.agents.shared.a2ui import A2UI_CUSTOM_NAME
from mcp_server_phytomni.mcp import app as mcp_app

_SENSITIVE_MARKERS = frozenset(
    {
        "bearer-secret",
        "postgresql://",
        "db-user:db-password",
        "SELECT secret_token",
    }
)


class StreamTestTools(NamedTuple):
    """Shared helpers and storage path for streaming tests."""

    extract_run_started_id: Callable[[str], str]
    extract_custom_a2ui: Callable[[str], dict[str, Any] | None]
    patch_chat_stream: Callable[[list[dict[str, Any]]], None]
    tasks_db_path: str


@pytest.fixture(name="extract_run_started_id")
def _extract_run_started_id_fixture() -> Callable[[str], str]:
    """Return a parser for the ``run_id`` in an SSE RunStarted frame."""

    def extract(body: str) -> str:
        """Return the ``run_id`` embedded in the SSE body."""
        marker = "event: RunStarted\ndata: "
        start = body.index(marker) + len(marker)
        end = body.index("\n", start)
        return str(json.loads(body[start:end])["run_id"])

    return extract


@pytest.fixture(name="extract_custom_a2ui")
def _extract_custom_a2ui_fixture() -> Callable[[str], dict[str, Any] | None]:
    """Return a parser for the ``phyto.a2ui`` custom SSE frame."""

    def extract(body: str) -> dict[str, Any] | None:
        """Return the A2UI object from an SSE body, if present."""
        for event, payload in parse_sse_frames(body):
            if event != "Custom":
                continue
            if payload.get("name") == A2UI_CUSTOM_NAME:
                value = payload.get("value")
                return value if isinstance(value, dict) else None
        return None

    return extract


@pytest.fixture(name="patch_chat_stream")
def _patch_chat_stream_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[dict[str, Any]]], None]:
    """Return a helper that patches the MCP chat provider stream."""

    def patch(payloads: list[dict[str, Any]]) -> None:
        """Replace the provider stream with the supplied payloads."""

        async def fake_stream(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            """Yield each pre-built payload, ignoring chat kwargs."""
            for payload in payloads:
                yield payload

        monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)

    return patch


@pytest.fixture
def stream_test_tools(
    extract_run_started_id: Callable[[str], str],
    extract_custom_a2ui: Callable[[str], dict[str, Any] | None],
    patch_chat_stream: Callable[[list[dict[str, Any]]], None],
    tasks_db_path: str,
) -> StreamTestTools:
    """Bundle streaming helpers when a test would exceed Pylint's arity."""
    return StreamTestTools(
        extract_run_started_id,
        extract_custom_a2ui,
        patch_chat_stream,
        tasks_db_path,
    )


@pytest.fixture(name="assert_failed_stream")
def _assert_failed_stream_fixture(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    extract_run_started_id: Callable[[str], str],
) -> Callable[[str], Awaitable[None]]:
    """Return shared assertions for sanitized failed SSE responses."""

    async def assert_failed(body: str) -> None:
        """Assert one sanitized error and its failed registry row."""
        assert body.count("event: RunError\n") == 1
        assert "event: RunFinished\n" not in body
        assert body.count("data: [DONE]") == 1
        assert not any(marker in body for marker in _SENSITIVE_MARKERS)
        run_id = extract_run_started_id(body)
        fetched = await api_client.get(
            f"/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "failed"

    return assert_failed


@pytest.fixture(name="review_response")
def _review_response() -> Callable[[], dict[str, Any]]:
    """Return the canonical successful Review graph response."""

    def build() -> dict[str, Any]:
        """Build an approved response for a resume assertion."""
        return {
            "final_response": {
                "choices": [
                    {
                        "message": {
                            "content": "Approved final review.",
                            "doc_list": [],
                            "follow_up_questions": [],
                        }
                    }
                ]
            }
        }

    return build


@pytest.fixture(name="review_app_factory")
def _review_app_factory(
    review_response: Callable[[], dict[str, Any]],
) -> Callable[..., Any]:
    """Return fake Review graph apps for pause and resume tests."""

    async def checkpoint(_config: dict[str, Any]) -> object:
        """Return a checkpoint sentinel for resume paths."""
        return object()

    def make(
        *, interrupt_key: str = "draft", reinterrupt: bool = False
    ) -> Any:
        """Build a fake app with configurable interrupt behavior."""
        calls: list[Any] = []
        success_response = review_response()

        async def ainvoke(
            payload: Any,
            *,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            """Pause on input and finish or re-interrupt on resume."""
            calls.append((payload, config))
            if isinstance(payload, dict):
                return {
                    "__interrupt__": [
                        SimpleNamespace(value={interrupt_key: "draft review"})
                    ]
                }
            if reinterrupt:
                return {
                    "__interrupt__": [
                        SimpleNamespace(value={"draft": "revised draft"})
                    ]
                }
            return success_response

        return SimpleNamespace(
            checkpointer=SimpleNamespace(aget=checkpoint),
            calls=calls,
            ainvoke=ainvoke,
            success_response=success_response,
        )

    return make
