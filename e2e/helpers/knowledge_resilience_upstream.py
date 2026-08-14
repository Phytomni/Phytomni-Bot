# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Loopback-only scripted upstream for Knowledge HTTP integration tests."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager
from typing import IO, Any, Literal, NamedTuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

KnowledgeScenarioMode = Literal[
    "complete",
    "partial_then_complete",
    "all_failed",
    "malformed",
]

_VALID_MODES = frozenset(
    {"complete", "partial_then_complete", "all_failed", "malformed"}
)
_SCENARIO_ENV = "PHYTOMNI_KNOWLEDGE_SCENARIO"
_INTEGRATION_ENV = "PHYTOMNI_RUN_INTEGRATION"
_CURRENT_QUERY = "synthetic protein design question"
_EARLIER_QUERY = "earlier question"
_EARLIER_ANSWER = "earlier answer"
_SECRET_MARKER = "synthetic-provider-secret"
_STARTUP_DEADLINE = 30.0


class KnowledgeUpstream(NamedTuple):
    """Connection details for one isolated scripted upstream."""

    base_url: str


class _ScenarioState:
    """In-memory counters and sanitized observations for one process."""

    def __init__(self, mode: KnowledgeScenarioMode) -> None:
        self.mode = mode
        self.lock = asyncio.Lock()
        self.retrieve_calls: dict[str, int] = {}
        self.retrieve_contents: list[str] = []
        self.rerank_calls = 0
        self.rerank_ids: list[list[str]] = []
        self.provider_role_sequences: list[list[str]] = []
        self.provider_history_markers: list[dict[str, bool]] = []

    def snapshot(self) -> dict[str, Any]:
        """Return synthetic-only observations; never return headers or bodies."""
        return {
            "mode": self.mode,
            "retrieve": {
                "calls": dict(sorted(self.retrieve_calls.items())),
                "contents": list(self.retrieve_contents),
            },
            "rerank": {
                "calls": self.rerank_calls,
                "ids": [list(ids) for ids in self.rerank_ids],
            },
            "provider": {
                "calls": len(self.provider_role_sequences),
                "role_sequences": [
                    list(roles) for roles in self.provider_role_sequences
                ],
                "history_markers": [
                    dict(markers) for markers in self.provider_history_markers
                ],
            },
        }


def _validate_mode(mode: str) -> KnowledgeScenarioMode:
    """Validate one scenario name without accepting arbitrary child input."""
    if mode not in _VALID_MODES:
        raise ValueError(f"unsupported Knowledge scenario: {mode!r}")
    return mode  # type: ignore[return-value]


def _require_child_environment(mode: str) -> KnowledgeScenarioMode:
    """Require the explicit integration gate before serving any request."""
    if os.environ.get(_INTEGRATION_ENV) != "1":
        raise RuntimeError("Knowledge upstream requires integration opt-in")
    configured = os.environ.get(_SCENARIO_ENV, mode)
    if configured != mode:
        raise RuntimeError("Knowledge upstream scenario mismatch")
    return _validate_mode(configured)


def _valid_document(repo_id: str) -> dict[str, Any]:
    """Build one deterministic retrieval document."""
    return {
        "chunk_id": f"{repo_id}-synthetic-chunk",
        "title": f"Synthetic evidence for {repo_id}",
        "content": "Synthetic evidence supports the protein design answer.",
        "score": 0.91,
    }


def _is_follow_up_prompt(messages: list[dict[str, Any]]) -> bool:
    """Identify the agent's fixed follow-up prompt without storing it."""
    return any(
        "follow-up" in str(message.get("content", "")).lower()
        or "follow up" in str(message.get("content", "")).lower()
        for message in messages
    )


def create_app(mode: str | None = None) -> FastAPI:
    """Create one scripted upstream app with isolated process state."""
    configured_mode = _validate_mode(
        mode or os.environ.get(_SCENARIO_ENV, "complete")
    )
    state = _ScenarioState(configured_mode)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _require_child_environment(configured_mode)
        yield

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Report readiness without exposing scenario state."""
        return {"status": "ok"}

    @app.get("/snapshot")
    async def snapshot() -> dict[str, Any]:
        """Return only bounded synthetic observations for the test."""
        async with state.lock:
            return state.snapshot()

    @app.post("/retrieve", response_model=None)
    async def retrieve(request: Request) -> JSONResponse | dict[str, Any]:
        """Serve deterministic per-repository retrieve outcomes."""
        payload = await request.json()
        repo_id = payload.get("repo_id")
        content = payload.get("content")
        scope = payload.get("scope")
        if not isinstance(repo_id, str) or not isinstance(content, str):
            return JSONResponse({"error": _SECRET_MARKER}, status_code=400)
        if scope != "doc":
            return JSONResponse({"error": _SECRET_MARKER}, status_code=400)

        async with state.lock:
            call_number = state.retrieve_calls.get(repo_id, 0) + 1
            state.retrieve_calls[repo_id] = call_number
            state.retrieve_contents.append(content)

        if (
            state.mode == "partial_then_complete"
            and repo_id == "repo-b"
            and call_number == 1
        ):
            return JSONResponse({"error": _SECRET_MARKER}, status_code=503)
        if state.mode == "all_failed":
            if "valid-empty" in content and repo_id == "repo-a":
                return {"doc_list": []}
            return JSONResponse({"error": _SECRET_MARKER}, status_code=503)
        if state.mode == "malformed" and "malformed-retrieve" in content:
            return {
                "doc_list": [
                    {"chunk_id": f"{repo_id}-malformed", "title": "broken"}
                ]
            }
        return {"doc_list": [_valid_document(repo_id)]}

    @app.post("/rerank", response_model=None)
    async def rerank(request: Request) -> JSONResponse | dict[str, Any]:
        """Return valid scores or one deliberately invalid rank payload."""
        payload = await request.json()
        query = payload.get("query")
        docs = payload.get("docs")
        if not isinstance(query, str) or not isinstance(docs, list):
            return JSONResponse({"error": _SECRET_MARKER}, status_code=400)
        ids = [
            item.get("id")
            for item in docs
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        async with state.lock:
            state.rerank_calls += 1
            state.rerank_ids.append(list(ids))
        if state.mode == "malformed" and "malformed-rerank" in query:
            return {
                "rank_result": [{"id": "unknown-synthetic-id", "score": 0.91}],
                "error": _SECRET_MARKER,
            }
        return {
            "rank_result": [
                {"id": item_id, "score": 0.91 - index * 0.01}
                for index, item_id in enumerate(ids)
            ]
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> dict[str, Any]:
        """Return one canonical non-streaming synthetic completion."""
        payload = await request.json()
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return {"error": _SECRET_MARKER}
        normalized_messages = [
            message for message in messages if isinstance(message, dict)
        ]
        roles = [
            role
            for message in normalized_messages
            if isinstance((role := message.get("role")), str)
        ]
        markers = {
            "current_query": any(
                _CURRENT_QUERY in str(message.get("content", ""))
                for message in normalized_messages
            ),
            "earlier_query": any(
                message.get("content") == _EARLIER_QUERY
                for message in normalized_messages
            ),
            "earlier_answer": any(
                message.get("content") == _EARLIER_ANSWER
                for message in normalized_messages
            ),
        }
        async with state.lock:
            state.provider_role_sequences.append(roles)
            state.provider_history_markers.append(markers)
        content = (
            "[]"
            if _is_follow_up_prompt(normalized_messages)
            else ("Synthetic protein design answer [1]")
        )
        return {
            "id": "chatcmpl-synthetic-knowledge",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", "knowledge-resilience-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    return app


app = create_app()


def _free_port() -> int:
    """Return an available loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _drain(stream: IO[str], logs: deque[str]) -> None:
    """Keep only a bounded sanitized subprocess log tail."""
    for line in stream:
        logs.append(line.rstrip("\n"))


def _await_healthy(
    proc: subprocess.Popen[str], base_url: str, logs: deque[str]
) -> None:
    """Wait for loopback health or fail with bounded startup logs."""
    deadline = time.monotonic() + _STARTUP_DEADLINE
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Knowledge upstream exited with code {proc.returncode}; "
                f"logs:\n{chr(10).join(logs)}"
            )
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=2.0)
            if response.status_code == 200 and response.json() == {
                "status": "ok"
            }:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(
        f"Knowledge upstream did not become healthy; logs:\n{chr(10).join(logs)}"
    )


def _require_parent_environment(mode: KnowledgeScenarioMode) -> None:
    """Reject accidental live or non-integration use of this helper."""
    if os.environ.get(_INTEGRATION_ENV) != "1":
        raise RuntimeError("Knowledge upstream requires integration opt-in")
    _validate_mode(mode)


@contextmanager
def boot_knowledge_resilience_upstream(
    mode: KnowledgeScenarioMode,
) -> Generator[KnowledgeUpstream, None, None]:
    """Boot a fresh loopback upstream and terminate it on every exit."""
    _require_parent_environment(mode)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env[_INTEGRATION_ENV] = "1"
    env[_SCENARIO_ENV] = mode
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        f"{__name__}:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    logs: deque[str] = deque(maxlen=200)
    with subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        drain = threading.Thread(
            target=_drain, args=(proc.stdout, logs), daemon=True
        )
        drain.start()
        try:
            _await_healthy(proc, base_url, logs)
            yield KnowledgeUpstream(base_url)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        drain.join(timeout=5)


__all__ = [
    "KnowledgeScenarioMode",
    "KnowledgeUpstream",
    "app",
    "boot_knowledge_resilience_upstream",
    "create_app",
]
