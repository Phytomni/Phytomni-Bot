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

import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import (
    Any,
    Self,
    cast,
)

import httpx
import pytest
from starlette.responses import StreamingResponse
from tests.support.attachment_fakes import (
    managed_document_evidence_item,
)
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    parse_sse_frames,
)

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api import streaming as streaming_runtime
from mcp_server_phytomni.api.app import _stream_chat_completion
from mcp_server_phytomni.api.attachments import (
    ManagedAttachmentEvidence,
    redact_streaming_attachment_response,
)
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp.result_formatting import (
    run_finished,
    run_started,
    text_message_content,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _conversation_envelope(*, turn_id: str = "21") -> ConversationEnvelopeV1:
    """Build one Instant V1 envelope for streaming tests."""
    return ConversationEnvelopeV1.model_validate(
        build_instant_chat_context_envelope(
            turn_id,
            ledger_cursor=int(turn_id),
        )
    )


def _extract_custom_context(body: str) -> dict[str, Any] | None:
    """Return the staged context payload from one SSE body, if present."""
    marker = "event: Custom\ndata: "
    for chunk in body.split("\n\n"):
        if not chunk.startswith(marker):
            continue
        payload = json.loads(chunk.removeprefix(marker))
        if payload.get("name") == "phyto.context_staged":
            value = payload.get("value")
            return value if isinstance(value, dict) else None
    return None


def _runtime_context_service() -> Any:
    """Resolve the runtime context service through its public test seam."""
    return getattr(streaming_runtime, "context_service")()


async def _consume_context_stage(
    response: Any,
    payload: ChatCompletionRequest,
    db_path: str,
    run_id: str,
    answer_marker: str,
) -> tuple[str, dict[str, Any]]:
    """Consume through the staged frame and verify durable pre-finish state."""
    body = cast(AsyncGenerator[str, None], response.body_iterator)
    accumulated = ""
    async for line in body:
        accumulated += line
        if '"name": "phyto.context_staged"' not in line:
            continue
        record = RunRegistry(db_path=db_path).get_run(run_id, owner="u1")
        assert record is not None
        assert record.status == "succeeded"
        assert payload.conversation is not None
        conversation_data = vars(payload.conversation)
        stored_turn = ConversationContextStore(db_path).load_turn(
            str(conversation_data["conversation_key"]),
            conversation_data["turn_id"],
        )
        assert stored_turn is not None
        assert stored_turn.state == "staged"
        assert stored_turn.delta is not None
        assert answer_marker not in json.dumps(
            stored_turn.delta, sort_keys=True
        )
        assert answer_marker in json.dumps(stored_turn.result, sort_keys=True)
        assert stored_turn.stage_metadata is not None
        assert stored_turn.stage_metadata["selected_agent_id"] == "ChatAgent"
        assert stored_turn.stage_metadata["route_source"] == "instant_lock"
        break
    else:
        raise AssertionError("phyto.context_staged was not emitted")
    accumulated += "".join([line async for line in body])
    assert payload.conversation is not None
    return accumulated, vars(payload.conversation)


def _stream_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into semantic ``(event, payload)`` pairs."""
    return parse_sse_frames(body)


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
    assert records[-1].result is not None
    assert records[-1].result["formatted"] == {"answer": ""}
    assert records[-1].result["execution"]["tracking"] == {"degraded": False}
    assert records[-1].result["raw"] is None
    assert records[-1].result["stream"] is True
    assert records[-1].result["partial"] is True


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
    original_settle = getattr(api_app, "_settle_stream_run")

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


class _CloseableStream:
    """Async body iterator that records whether ``aclose`` ran."""

    def __init__(self, chunks: list[str]) -> None:
        self.closed = False
        self._chunks = list(chunks)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> str:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        """Mark the source iterator closed for cancellation assertions."""
        self.closed = True


async def test_streaming_attachment_redaction_spans_chunk_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE redaction replaces split references and propagates aclose."""
    del monkeypatch
    reference = "/obs/resolver-bucket/agent_data/uploads/u1/file_deadbeef"
    source = _CloseableStream(
        [
            f'data: {{"answer":"prefix {reference[:12]}',
            reference[12:28],
            f'{reference[28:]} suffix"}}\n\ndata: [DONE]\n\n',
        ]
    )
    evidence = ManagedAttachmentEvidence(
        attachment_owner="u1",
        items=(
            managed_document_evidence_item(
                asset_id="file_stream_document",
                reference=reference,
            ),
        ),
    )
    response = redact_streaming_attachment_response(
        StreamingResponse(source, media_type="text/event-stream"),
        evidence,
    )
    assert isinstance(response, StreamingResponse)
    body = cast(AsyncGenerator[Any, None], response.body_iterator)
    chunks: list[str] = []
    async for chunk in body:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
        if "data: [DONE]" in "".join(chunks):
            await body.aclose()
            break
    reconstructed = "".join(chunks)
    assert reference not in reconstructed
    assert "<redacted-attachment>" in reconstructed
    assert "data: " in reconstructed
    assert "data: [DONE]\n\n" in reconstructed
    assert source.closed is True
