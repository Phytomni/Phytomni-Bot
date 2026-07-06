# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``POST /v1/chat/completions`` with ``stream=true``.

Pins AG-UI SSE framing (``RunStarted`` / ``TextMessageContent`` /
``RunFinished`` plus the trailing ``[DONE]``), per-model gating,
``resolve_gene_id`` rejection, and the two-stage run write: a
``running`` row is written before the first frame and settled to
``succeeded``/``failed`` from the stream wrapper's ``finally`` block
once the response drains.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from typing import (
    Any,
    cast,
)

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app import _stream_chat_completion
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import (
    run_finished,
    run_started,
    text_message_content,
)
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _patch_chat_stream(
    monkeypatch: pytest.MonkeyPatch, payloads: list[dict[str, Any]]
) -> None:
    """Replace ``stream_phyto_chat_chunks`` in the mcp app namespace.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        payloads: Provider chunk dicts the fake should yield.
    """

    async def fake_stream(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Yield each pre-built payload, ignoring the chat kwargs."""
        for payload in payloads:
            yield payload

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)


def _extract_run_started_id(body: str) -> str:
    """Return the ``run_id`` embedded in the SSE body's RunStarted frame."""
    marker = "event: RunStarted\ndata: "
    start = body.index(marker) + len(marker)
    end = body.index("\n", start)
    return str(json.loads(body[start:end])["run_id"])


async def test_stream_phyto_chat_emits_agui_frames(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream=true`` + ``phyto-chat`` yields AG-UI SSE frames.

    Pins the wire format: ``text/event-stream`` content-type, an
    opening ``RunStarted`` frame, at least one ``TextMessageContent``
    delta frame per non-empty upstream chunk, a closing
    ``RunFinished`` frame, and the terminating ``data: [DONE]`` line
    every SSE consumer relies on to close its ``EventSource``.
    """
    _patch_chat_stream(
        monkeypatch,
        [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {
                "choices": [
                    {"delta": {"content": "lo"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(api_client, issued_api_key, stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: RunStarted\n" in body
    assert "event: TextMessageContent\n" in body
    assert "event: RunFinished\n" in body
    assert body.rstrip().endswith("data: [DONE]")


async def test_stream_with_resolve_gene_id_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """``resolve_gene_id=true`` + ``stream=true`` surfaces a 400.

    Pins the BriefGene-only gate: ``resolve_gene_id`` is valid only
    for BriefGene calls (``_maybe_resolve_brief_gene_query`` raises
    400 on misuse), and BriefGene is not in
    ``_STREAM_CAPABLE_TOOLS``. The combination is therefore always
    unreachable — ``resolve_gene_id=true`` against ``phyto-chat``
    yields the "resolve_gene_id is only valid for BriefGene calls"
    400, exercised here.
    """
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-chat",
        stream=True,
        resolve_gene_id=True,
    )

    assert response.status_code == 400
    assert "resolve_gene_id" in response.json()["error"]["message"]


async def test_stream_run_settles_succeeded_after_finish(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """The run settles ``succeeded`` after the stream reaches RunFinished.

    Pins the two-stage run write: stage 1 pre-mints ``run_id`` and
    writes a ``running`` row before the first frame; stage 2 settles
    the same row to ``succeeded`` from the ``finally`` block once the
    wrapper observes the ``RunFinished`` marker. Client history
    queries through ``GET /v1/runs`` must surface exactly one row for
    the call, carrying the request-scoped ``dialogue_id`` and the
    ``origin="local"`` stamp sync agents use.

    Also cross-checks two settle-path contracts: the ``run_id`` carried
    by the opening ``RunStarted`` SSE frame is the same id the registry
    persisted (not a placeholder unrelated to the polled row), and the
    settled row's ``created_at`` was never advanced past its own
    ``updated_at`` — the regression this test guards settled the run
    via ``create_run``'s INSERT OR REPLACE, which stamps ``now`` into
    both columns and would otherwise let a stale ``created_at`` slip
    past ``updated_at`` on a slow settle.
    """
    _patch_chat_stream(
        monkeypatch,
        [
            {
                "choices": [
                    {"delta": {"content": "Hi"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(
        api_client, issued_api_key, stream=True, dialogue_id="dlg-s1"
    )

    assert response.status_code == 200
    started_run_id = _extract_run_started_id(response.text)
    registry = RunRegistry(db_path=tasks_db_path)
    # issued_api_key fixture binds the key to user "u1"; the request
    # context resolves the owner from the authenticated principal.
    runs = [
        r
        for r in registry.list_runs(owner="u1")
        if r.spec.agent == "chat" and r.request_info
    ]
    assert len(runs) == 1
    record = runs[0]
    assert record.status == "succeeded"
    assert record.request_info.dialogue_id == "dlg-s1"
    assert record.spec.origin == "local"
    assert record.spec.run_id == started_run_id
    assert record.timestamps.created_at <= record.timestamps.updated_at


async def _drive_stream_until(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_after_finish: bool,
) -> str | None:
    """Drive ``_stream_chat_completion``'s generator then ``aclose`` early.

    Patches the producer to a deterministic ``AguiEvent`` sequence,
    drives the ``StreamingResponse`` body iterator to just after (or
    just before) the ``RunFinished`` frame, aborts via ``aclose`` to
    simulate a client disconnect, and returns the settled run status
    read back from the registry.

    Args:
        tasks_db_path: Temp SQLite path the handlers resolve to.
        monkeypatch: Pytest monkeypatch fixture.
        stop_after_finish: When True, consume through the
            ``RunFinished`` line before aborting; when False, abort at
            the first ``TextMessageContent`` line, before
            ``RunFinished`` is ever produced.

    Returns:
        The settled run's status, or None when no row was created.
    """
    captured: dict[str, str] = {}

    async def fake_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        """Yield a fixed RunStarted/TextMessageContent/RunFinished run."""
        captured["run_id"] = run_id
        yield run_started(run_id, dialogue_id)
        yield text_message_content("m-d", "Hi")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "invoke_tool_streamed", fake_streamed)

    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="hi")],
        stream=True,
        dialogue_id="dlg-p2s4",
    )
    with request_context("u1", "req-p2s4"):
        response = _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={"user_query": "hi", "obs_file_list": []},
            payload=payload,
            user_query="hi",
        )
        body = cast(AsyncGenerator[str, None], response.body_iterator)
        async for line in body:
            if not stop_after_finish and "event: TextMessageContent\n" in line:
                await body.aclose()
                break
            if stop_after_finish and "event: RunFinished\n" in line:
                await body.aclose()
                break
        else:
            await body.aclose()
        run_id = captured["run_id"]
    registry = RunRegistry(db_path=tasks_db_path)
    record = registry.get_run(run_id, owner="u1")
    return record.status if record is not None else None


async def test_stream_run_succeeds_when_client_disconnects_after_finish(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect AFTER RunFinished still settles the run succeeded."""
    status = await _drive_stream_until(
        tasks_db_path, monkeypatch, stop_after_finish=True
    )
    assert status == "succeeded"


async def test_stream_run_fails_when_client_disconnects_before_finish(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect BEFORE RunFinished settles the run failed."""
    status = await _drive_stream_until(
        tasks_db_path, monkeypatch, stop_after_finish=False
    )
    assert status == "failed"


def _guard_network_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast on any un-mocked raw socket in a streaming test.

    The ``api_client`` fixture dispatches in-process over
    ``httpx.ASGITransport``, which itself flows through
    ``httpx.AsyncClient.send`` — so ``send`` cannot be blocked here
    without severing the test's own transport. Instead this guards the
    layer BENEATH httpx: the event loop's ``create_connection`` and DNS
    ``getaddrinfo``, which only fire on a real outbound socket. The
    knowledge stream drives a fake ``astream``, so no real graph or
    network call should reach that layer; an escape surfaces as a named
    error rather than a hang (see the repo-root ``block_external_http``
    coverage gap, which patches only sync ``socket.create_connection``
    and ``httpx.*.request``). Built as one raising factory over a
    (loop-method, label) table so the two guarded paths share one code
    path rather than two near-identical closures.
    """
    loop_cls = asyncio.base_events.BaseEventLoop
    guarded = (
        ("create_connection", "a raw async socket"),
        ("getaddrinfo", "DNS resolution"),
    )

    def _make_raiser(label: str) -> Any:
        """Return a callable raising a named offline-escape error."""

        def _raise(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError(
                f"offline stream test escaped to {label}; a mock is missing"
            )

        return _raise

    for method_name, label in guarded:
        monkeypatch.setattr(loop_cls, method_name, _make_raiser(label))


class _FakeKnowledgeStreamApp:
    """Fake compiled KnowledgeAgent graph asserting astream config.

    Records the ``config`` passed to ``astream`` so the test can prove
    Step 0's ``build_runnable_config`` fix reached the seam, then yields
    one whitelisted stage update (``retrieve_node`` -> ``retrieving``)
    plus one cited terminal ``values`` chunk so the terminal projection
    emits ``TextMessageContent`` + ``Custom`` frames.
    """

    def __init__(self) -> None:
        """Init the captured-config holder."""
        self.captured_config: Mapping[str, Any] | None = None

    def thread_id(self) -> str | None:
        """Return the thread_id astream received, or None if unset.

        A public accessor (beyond ``astream``) both reads cleaner at the
        call site and keeps this streaming-test fake off the R0903
        single-public-method baseline the sibling fakes are pinned to.
        """
        if self.captured_config is None:
            return None
        configurable = self.captured_config.get("configurable") or {}
        return configurable.get("thread_id")

    async def astream(
        self,
        _state: Mapping[str, Any],
        stream_mode: list[str],
        config: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Record config, then yield one stage + one terminal chunk."""
        assert stream_mode == ["updates", "values"]
        self.captured_config = config
        yield ("updates", {"retrieve_node": {}})
        yield (
            "values",
            {
                "final_response": {
                    "choices": [
                        {
                            "message": {
                                "content": "Rice photosynthesis [1].",
                                "doc_list": [{"file_id": "f1", "title": "T1"}],
                                "follow_up_questions": ["next?"],
                            }
                        }
                    ]
                }
            },
        )


async def test_stream_phyto_knowledge_emits_agui_frames(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream=true`` + ``phyto-knowledge`` yields graph AG-UI frames.

    Routes through the real ``_build_graph_stream_target`` ->
    ``_stream_graph_agent`` seam with the registry accessor replaced by a
    fake app (no real graph), asserting the SSE body carries the stage
    ``StepStarted`` frame, the terminal ``TextMessageContent`` answer, the
    ``Custom`` references frame, and the closing ``RunFinished``. The
    fake's ``astream`` also asserts it received a ``config`` whose
    ``thread_id`` is the streamed run's id — the HTTP-layer proof of
    Step 0.
    """
    _guard_network_escape(monkeypatch)
    fake_app = _FakeKnowledgeStreamApp()

    def _fake_target(
        _user_query: str, obs_file_list: Any = None
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app + a minimal knowledge initial state."""
        del obs_file_list
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "knowledge_stream_target", _fake_target)

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment so the test stays offline."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)

    response = await chat_completion(
        api_client, issued_api_key, model="phyto-knowledge", stream=True
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: RunStarted\n" in body
    assert "event: StepStarted\n" in body
    assert "event: TextMessageContent\n" in body
    assert "event: Custom\n" in body
    assert "event: RunFinished\n" in body
    assert body.rstrip().endswith("data: [DONE]")
    started_run_id = _extract_run_started_id(body)
    assert fake_app.thread_id() == started_run_id


async def test_streamed_knowledge_run_reconcile_short_circuits(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A settled streamed run skips ``reconcile_task`` on ``GET /runs/{id}``.

    Drives one streamed ``phyto-knowledge`` run to ``RunFinished`` so it
    settles terminal, then fetches it via ``GET /v1/runs/{run_id}`` and
    asserts ``reconcile_task`` (mocked at the ``runtime.run_registry``
    import site) is never called: ``RunRegistry.reconcile`` short-circuits
    on a terminal cached run instead of probing live ``task_status``.
    """
    _guard_network_escape(monkeypatch)
    fake_app = _FakeKnowledgeStreamApp()

    def _fake_target(
        _user_query: str, obs_file_list: Any = None
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app + a minimal knowledge initial state."""
        del obs_file_list
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "knowledge_stream_target", _fake_target)

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment so the test stays offline."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)

    async def _boom_reconcile(_task_id: str) -> dict[str, Any]:
        """Fail loudly if reconcile fires on a terminal run."""
        raise AssertionError(
            "reconcile_task must not run for a terminal streamed run"
        )

    monkeypatch.setattr(run_registry_module, "reconcile_task", _boom_reconcile)

    response = await chat_completion(
        api_client, issued_api_key, model="phyto-knowledge", stream=True
    )
    assert response.status_code == 200
    run_id = _extract_run_started_id(response.text)

    # The run settled terminal in the shared temp DB, so reconcile must
    # short-circuit rather than probe. Read the row back through the same
    # path the fixture pins so the assertion runs against the DB the API
    # actually wrote to.
    registry = RunRegistry(db_path=tasks_db_path)
    settled = registry.get_run(run_id, owner="u1")
    assert settled is not None
    assert settled.status == "succeeded"

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"
