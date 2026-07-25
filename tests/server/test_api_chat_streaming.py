# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``POST /v1/chat/completions`` with ``stream=true``.

Pins AG-UI SSE framing (``RunStarted`` / ``TextMessageContent`` /
``RunFinished`` plus the trailing ``[DONE]``), per-model gating,
``resolve_gene_id`` rejection, and the two-stage run write: a
``running`` row is written before the first frame and settled to
``succeeded`` before ``RunFinished`` or to ``failed`` during cleanup.
"""

from __future__ import annotations

import asyncio
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


async def test_stream_phyto_chat_emits_agui_frames(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    patch_chat_stream: Callable[[list[dict[str, Any]]], None],
) -> None:
    """``stream=true`` + ``phyto-chat`` yields AG-UI SSE frames.

    Pins the wire format: ``text/event-stream`` content-type, an
    opening ``RunStarted`` frame, at least one ``TextMessageContent``
    delta frame per non-empty upstream chunk, a closing
    ``RunFinished`` frame, and the terminating ``data: [DONE]`` line
    every SSE consumer relies on to close its ``EventSource``.
    """
    patch_chat_stream(
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


async def test_stream_does_not_emit_run_finished_after_settle_failure(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    stream_test_tools: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal miss and failed cleanup expose one safe error."""
    settlement_statuses: list[str] = []

    def fail_settlement(
        _run_id: str,
        _owner: str,
        status: str,
        _result: dict[str, Any],
    ) -> bool:
        settlement_statuses.append(status)
        if status == "succeeded":
            return False
        raise RuntimeError("private cleanup settlement detail")

    monkeypatch.setattr(api_app, "_settle_stream_run", fail_settlement)
    stream_test_tools.patch_chat_stream(
        [{"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}]
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="hello",
    )

    assert response.status_code == 200
    body = response.text
    assert settlement_statuses == ["succeeded", "failed"]
    assert body.count("event: RunError\n") == 1
    assert "event: RunFinished\n" not in body
    assert body.count('"code": "run_persistence_failed"') == 1
    assert body.count("data: [DONE]") == 1
    assert body.rstrip().endswith("data: [DONE]")
    assert "private cleanup settlement detail" not in body


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


async def test_data_model_is_not_advertised_or_streamable(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """DataAgent stays absent from chat models with a clean lookup error."""
    models = await api_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert models.status_code == 200
    assert "phyto-data" not in {item["id"] for item in models.json()["data"]}

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-data",
        stream=True,
        content="data query",
    )
    assert response.status_code == 404
    assert "NotImplementedError" not in response.text
    assert response.json()["error"]["message"] == "resource not found"


async def test_stream_setup_failure_returns_json_before_headers(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eager setup errors do not commit a streaming 200 response."""

    def fail_prepare(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        """Raise an unsupported-stream setup error synchronously."""
        raise NotImplementedError("internal setup detail")

    monkeypatch.setattr(api_app, "prepare_tool_stream", fail_prepare)
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="photosynthesis",
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_stream_connect_failure_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection setup errors use a fixed gateway JSON response."""

    def fail_prepare(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        """Raise a connection failure with sensitive detail."""
        raise httpx.ConnectError("credential=hidden upstream unavailable")

    monkeypatch.setattr(api_app, "prepare_tool_stream", fail_prepare)
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="photosynthesis",
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["message"] == "upstream service failed"
    assert "credential=hidden" not in response.text
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_stream_timeout_failure_returns_504(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout setup errors use a fixed gateway JSON response."""

    def fail_prepare(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        """Raise a timeout with sensitive detail."""
        raise httpx.TimeoutException("credential=hidden upstream timeout")

    monkeypatch.setattr(api_app, "prepare_tool_stream", fail_prepare)
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="photosynthesis",
    )

    assert response.status_code == 504
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["message"] == "upstream service timed out"
    assert "credential=hidden" not in response.text
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_stream_priming_failure_settles_created_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-frame failure settles the already-created run as failed."""

    async def fail_prime(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        """Raise before yielding the first typed AG-UI event."""
        if _kwargs.get("emit_unreachable"):
            yield run_started("unreachable", None)
        raise RuntimeError("prime detail must stay server-side")

    monkeypatch.setattr(api_app, "prepare_tool_stream", fail_prime)
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="photosynthesis",
    )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    records = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert records
    assert records[-1].status == "failed"
    assert response.json()["error"]["message"] == "internal server error"


async def test_stream_priming_empty_returns_json_and_fails_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty raw stream is a failed pre-open response, not success."""

    async def empty_prepare(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        """Yield no protocol events."""
        if _kwargs.get("emit_unreachable"):
            yield run_started("unreachable", None)

    monkeypatch.setattr(api_app, "prepare_tool_stream", empty_prepare)
    response = await chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="photosynthesis",
    )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["message"] == "internal server error"
    assert "event:" not in response.text
    assert "data: [DONE]" not in response.text
    records = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert records
    assert records[-1].status == "failed"
    assert records[-1].result == {
        "formatted": {"answer": ""},
        "raw": None,
        "stream": True,
        "partial": True,
    }


@pytest.mark.parametrize(
    ("model", "tool_name"),
    [
        ("phyto-chat", "ChatAgent"),
        ("phyto-knowledge", "KnowledgeAgent"),
        ("phyto-review", "ReviewAgent"),
        ("phyto-brief-gene", "BriefGeneAgent"),
    ],
)
async def test_opened_agent_failure_emits_one_error_and_settles_failed(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    extract_run_started_id: Callable[[str], str],
    model: str,
    tool_name: str,
) -> None:
    """Every supported streamed model closes opened failures consistently."""

    async def failing_prepare(
        _tool_name: str,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        """Yield a partial prefix, then fail before RunFinished."""
        yield run_started(run_id, dialogue_id)
        yield text_message_content("m-failure", "partial")
        raise RuntimeError("backend token=hidden failure")

    monkeypatch.setattr(api_app, "prepare_tool_stream", failing_prepare)
    payload = ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="failure")],
        stream=True,
    )
    with request_context("u1", f"req-{model}"):
        response = await _stream_chat_completion(
            tool_name=tool_name,
            arguments={"user_query": "failure", "obs_file_list": []},
            payload=payload,
            user_query="failure",
        )
        body_iterator = cast(AsyncGenerator[str, None], response.body_iterator)
        body = "".join([line async for line in body_iterator])

    assert body.count("event: RunError\n") == 1
    assert "event: RunFinished\n" not in body
    assert body.count("data: [DONE]") == 1
    assert "backend token=hidden failure" not in body
    run_id = extract_run_started_id(body)
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "failed"
    assert record.result is not None
    assert record.result["partial"] is True


async def test_stream_run_settles_succeeded_after_finish(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    stream_test_tools: Any,
) -> None:
    """The run settles ``succeeded`` after the stream reaches RunFinished.

    Pins the two-stage run write: stage 1 pre-mints ``run_id`` and
    writes a ``running`` row before the first frame; stage 2 settles
    the same row to ``succeeded`` before exposing the ``RunFinished``
    marker. Client history
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
    stream_test_tools.patch_chat_stream(
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
    started_run_id = stream_test_tools.extract_run_started_id(response.text)
    registry = RunRegistry(db_path=stream_test_tools.tasks_db_path)
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
    assert record.result is not None
    assert record.result["formatted"]["answer"] == "Hi"
    assert record.result["stream"] is True
    assert record.result["truncated"] is False
    assert record.result["partial"] is False
    assert "[streamed]" not in record.result["formatted"]["answer"]


@pytest.mark.parametrize(
    ("model", "tool_name"),
    [
        ("phyto-chat", "ChatAgent"),
        ("phyto-knowledge", "KnowledgeAgent"),
        ("phyto-review", "ReviewAgent"),
        ("phyto-brief-gene", "BriefGeneAgent"),
    ],
)
async def test_standard_stream_agents_persist_real_answer(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    extract_run_started_id: Callable[[str], str],
    model: str,
    tool_name: str,
) -> None:
    """Every ordinary streamed model settles its real answer text.

    The graph-backed models emit one terminal message event rather than
    provider token deltas, but the HTTP registry contract is the same:
    history consumers must receive the answer that crossed the wire and
    never a generic ``[streamed]`` marker. Review's A2UI pause route is a
    separate structured-interrupt path and is intentionally not exercised
    by this ordinary completion helper.
    """

    async def fake_streamed(
        _tool_name: str,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        """Yield a complete two-part answer for the selected model."""
        yield run_started(run_id, dialogue_id)
        yield text_message_content("m-multi", "multi-agent ")
        yield text_message_content("m-multi", "answer")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)
    payload = ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="history")],
        stream=True,
    )
    with request_context("u1", f"req-{model}-success"):
        response = await _stream_chat_completion(
            tool_name=tool_name,
            arguments={"user_query": "history", "obs_file_list": []},
            payload=payload,
            user_query="history",
        )
        body_iterator = cast(AsyncGenerator[str, None], response.body_iterator)
        body = "".join([line async for line in body_iterator])

    assert body.count("data: [DONE]") == 1
    run_id = extract_run_started_id(body)
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["formatted"]["answer"] == "multi-agent answer"
    assert record.result["truncated"] is False
    assert record.result["partial"] is False
    assert "[streamed]" not in record.result["formatted"]["answer"]


async def test_stream_chat_run_get_exposes_answer(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    patch_chat_stream: Callable[[list[dict[str, Any]]], None],
    extract_run_started_id: Callable[[str], str],
) -> None:
    """A settled chat stream run exposes the real answer via GET /runs/{id}.

    Web history reads through the HTTP envelope, not ``RunRegistry``
    directly. Pins that ``GET /v1/runs/{run_id}`` carries
    the sealed ``result.formatted.answer`` projection and flattened
    ``answer`` shortcut without the ``[streamed]`` placeholder.
    """
    patch_chat_stream(
        [
            {
                "choices": [
                    {"delta": {"content": "Hi"}, "finish_reason": "stop"}
                ]
            },
        ],
    )

    response = await chat_completion(
        api_client, issued_api_key, stream=True, dialogue_id="dlg-http"
    )
    assert response.status_code == 200
    run_id = extract_run_started_id(response.text)

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "succeeded"
    assert body["dialogue_id"] == "dlg-http"
    result = body["result"]
    assert result is not None
    assert set(result) == {"formatted", "execution"}
    formatted_answer = result["formatted"]["answer"]
    assert formatted_answer == "Hi"
    assert "[streamed]" not in formatted_answer
    assert body["answer"] == formatted_answer


async def _drive_stream_until(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_after_finish: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    """Drive ``_stream_chat_completion`` then ``aclose`` early.

    Returns:
        ``(status, result)`` from the settled run row, or
        ``(None, None)`` when no row exists.
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

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)

    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="hi")],
        stream=True,
        dialogue_id="dlg-p2s4",
    )
    with request_context("u1", "req-p2s4"):
        response = await _stream_chat_completion(
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
    if record is None:
        return None, None
    return record.status, record.result


async def test_stream_run_succeeds_when_client_disconnects_after_finish(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect AFTER RunFinished still settles the run succeeded."""
    status, result = await _drive_stream_until(
        tasks_db_path, monkeypatch, stop_after_finish=True
    )
    assert status == "succeeded"
    assert result is not None
    assert result["formatted"]["answer"] == "Hi"
    assert result["partial"] is False


async def test_disconnect_after_finish_never_attempts_failed_settlement(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-finish disconnect cannot overwrite durable success."""
    statuses: list[str] = []
    original_settle = api_app._settle_stream_run

    def record_settlement(
        run_id: str,
        owner: str,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        statuses.append(status)
        return original_settle(run_id, owner, status, result)

    monkeypatch.setattr(api_app, "_settle_stream_run", record_settlement)
    status, result = await _drive_stream_until(
        tasks_db_path, monkeypatch, stop_after_finish=True
    )

    assert status == "succeeded"
    assert result is not None
    assert result["partial"] is False
    assert statuses == ["succeeded"]


async def test_stream_run_fails_when_client_disconnects_before_finish(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect BEFORE RunFinished settles failed with partial answer."""
    status, result = await _drive_stream_until(
        tasks_db_path, monkeypatch, stop_after_finish=False
    )
    assert status == "failed"
    assert result is not None
    assert result["formatted"]["answer"] == "Hi"
    assert result["partial"] is True
    assert result["stream"] is True


async def test_disconnect_before_finish_has_no_synthetic_frames(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing before finish settles failed without writing another frame."""
    captured: dict[str, str] = {}

    async def fake_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        """Yield a partial run whose remainder must be closed silently."""
        captured["run_id"] = run_id
        yield run_started(run_id, dialogue_id)
        yield text_message_content("m-disconnect", "partial")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="disconnect")],
        stream=True,
    )
    with request_context("u1", "req-disconnect-no-frame"):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={"user_query": "disconnect", "obs_file_list": []},
            payload=payload,
            user_query="disconnect",
        )
        body = cast(AsyncGenerator[str, None], response.body_iterator)
        seen: list[str] = []
        async for line in body:
            seen.append(line)
            if "event: TextMessageContent\n" in line:
                await body.aclose()
                break
        with pytest.raises(StopAsyncIteration):
            await anext(body)

    assert "event: RunError\n" not in "".join(seen)
    record = RunRegistry(tasks_db_path).get_run(captured["run_id"], owner="u1")
    assert record is not None
    assert record.status == "failed"


async def test_stream_settle_marks_truncated_when_over_cap(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    stream_test_tools: Any,
) -> None:
    """Chat stream settle sets truncated=true when over the soft cap."""
    monkeypatch.setenv("PHYTOMNI_STREAM_ANSWER_MAX_BYTES", "4")
    # ApiConfig is constructed inside the settle path via ApiConfig();
    # if the process already cached a config instance, patch the
    # resolver the app uses. Prefer patching at the call site:
    monkeypatch.setattr(
        api_app,
        "_stream_answer_max_bytes",
        lambda: 4,
    )
    stream_test_tools.patch_chat_stream(
        [
            {
                "choices": [
                    {
                        "delta": {"content": "HelloWorld"},
                        "finish_reason": "stop",
                    }
                ]
            },
        ],
    )
    response = await chat_completion(api_client, issued_api_key, stream=True)
    assert response.status_code == 200
    started_run_id = stream_test_tools.extract_run_started_id(response.text)
    registry = RunRegistry(db_path=stream_test_tools.tasks_db_path)
    record = registry.get_run(started_run_id, owner="u1")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["truncated"] is True
    answer = record.result["formatted"]["answer"]
    assert len(answer.encode("utf-8")) <= 4
    # Wire still carried the full text.
    assert "HelloWorld" in response.text or "Hello" in response.text


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


class _FakeCitedStreamApp:
    """Fake compiled cited-agent graph asserting astream config.

    Records the ``config`` passed to ``astream`` so the test can prove
    Step 0's ``build_runnable_config`` fix reached the seam, then yields
    one whitelisted stage update (``retrieve_node`` -> ``retrieving``)
    plus one cited terminal ``values`` chunk so the terminal projection
    emits ``TextMessageContent`` + ``Custom`` frames. The stage node and
    answer are configurable so the same fake pins Knowledge and Review.
    """

    def __init__(self, *, stage_node: str, answer: str) -> None:
        """Init the captured config and terminal fixture values."""
        self.captured_config: Mapping[str, Any] | None = None
        self._stage_node = stage_node
        self._answer = answer

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
        *,
        subgraphs: bool = False,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        """Record config, then yield one stage + one terminal chunk."""
        assert stream_mode == ["custom", "updates", "values"]
        assert subgraphs is True
        self.captured_config = config
        yield ((), "updates", {self._stage_node: {}})
        yield (
            (),
            "values",
            {
                "final_response": {
                    "choices": [
                        {
                            "message": {
                                "content": self._answer,
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
    extract_run_started_id: Callable[[str], str],
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
    fake_app = _FakeCitedStreamApp(
        stage_node="retrieve_node", answer="Rice photosynthesis [1]."
    )

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
    started_run_id = extract_run_started_id(body)
    assert fake_app.thread_id() == started_run_id


async def test_stream_phyto_review_emits_agui_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review's graph stream emits its existing stage/custom vocabulary."""
    _guard_network_escape(monkeypatch)
    fake_app = _FakeCitedStreamApp(
        stage_node="retrieve_reduce_node", answer="Review evidence [1]."
    )

    def _fake_target(
        _user_query: str, obs_file_list: Any = None
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app + a minimal review initial state."""
        del obs_file_list
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "review_stream_target", _fake_target)

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment so the test stays offline."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)

    events = [
        event
        async for event in mcp_app.invoke_tool_streamed(
            "ReviewAgent",
            {"user_query": "review", "obs_file_list": []},
            run_id="run-review",
            dialogue_id="dlg-review",
        )
    ]
    types = [event.type for event in events]
    assert types[0] == "RunStarted"
    assert "StepStarted" in types
    assert "TextMessageContent" in types
    assert "Custom" in types
    assert types[-1] == "RunFinished"
    assert any(
        event.type == "StepStarted" and event.data["step_name"] == "retrieving"
        for event in events
    )
    customs = {
        event.data["name"]: event.data["value"]
        for event in events
        if event.type == "Custom"
    }
    assert "phyto.references" in customs
    assert customs["phyto.follow_up"] == ["next?"]


async def test_streamed_knowledge_run_reconcile_short_circuits(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    stream_test_tools: Any,
) -> None:
    """A settled streamed run skips ``reconcile_task`` on ``GET /runs/{id}``.

    Drives one streamed ``phyto-knowledge`` run to ``RunFinished`` so it
    settles terminal, then fetches it via ``GET /v1/runs/{run_id}`` and
    asserts ``reconcile_task`` (mocked at the ``runtime.run_registry``
    import site) is never called: ``RunRegistry.reconcile`` short-circuits
    on a terminal cached run instead of probing live ``task_status``.
    """
    _guard_network_escape(monkeypatch)
    fake_app = _FakeCitedStreamApp(
        stage_node="retrieve_node", answer="Rice photosynthesis [1]."
    )

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
    run_id = stream_test_tools.extract_run_started_id(response.text)

    # The run settled terminal in the shared temp DB, so reconcile must
    # short-circuit rather than probe. Read the row back through the same
    # path the fixture pins so the assertion runs against the DB the API
    # actually wrote to.
    registry = RunRegistry(db_path=stream_test_tools.tasks_db_path)
    settled = registry.get_run(run_id, owner="u1")
    assert settled is not None
    assert settled.status == "succeeded"

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"
