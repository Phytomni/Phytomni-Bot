# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Review wrapper and context-executor settlement tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.agents.review.conversation import (
    ReviewClarificationError,
    ReviewConversationAdapter,
    RevisedSection,
    extract_review_checkpoint,
    load_review_checkpoint,
    revise_section,
)
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextDelta,
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from tests.agents.test_review_conversation import (
    _CONVERSATION_KEY,
    _FULL_REPORT,
    _THREAD_ID,
    _agent_with_app,
    _candidate_review_state,
    _checkpoint_state,
    _follow_up_adapter,
    _projection,
    _stateful_review_agent,
)
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.runtime.conversation_context.test_service import (
    _envelope,
    _service,
)

pytestmark = pytest.mark.agent


@pytest.fixture(name="context_store")
def isolated_context_store(tmp_path: Any) -> ConversationContextStore:
    """Provide an isolated store for service replay probes."""
    return ConversationContextStore(str(tmp_path / "context.sqlite"))


@pytest.mark.asyncio
async def test_context_service_inspect_replay_is_read_only(
    context_store: ConversationContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay probes return durable state without callbacks or writes."""
    service = _service(context_store)
    envelope = _envelope()
    await service.execute_turn(envelope)
    staged = await service.inspect_replay(envelope)
    assert staged is not None
    assert staged.status is PrepareStatus.RETURN_STAGED
    await service.acknowledge_settlement(envelope, "b" * 64)

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("replay probe must not mutate or invoke")

    async def fail_callback(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("replay probe must not invoke callbacks")

    for name in ("begin_turn", "stage_turn", "mark_turn_failed"):
        monkeypatch.setattr(context_store, name, fail_if_called)
    for name in ("router", "invoke", "delegate_async"):
        monkeypatch.setattr(service, name, fail_callback)

    committed = await service.inspect_replay(envelope)
    assert committed is not None
    assert committed.status is PrepareStatus.RETURN_COMMITTED
    assert await service.inspect_replay(_envelope(turn_id="2")) is None


@pytest.mark.parametrize(
    "changed_fields",
    [{"operation": "replace"}, {"base_business_context_version": 1}],
)
@pytest.mark.asyncio
async def test_context_service_inspect_replay_rejects_changed_duplicate(
    context_store: ConversationContextStore,
    changed_fields: dict[str, Any],
) -> None:
    """Replay probes treat a changed proposal as no matching replay."""
    service = _service(context_store)
    envelope = _envelope()
    await service.execute_turn(envelope)
    assert (
        await service.inspect_replay(
            envelope.model_copy(update=changed_fields)
        )
        is None
    )


def test_scope_change_stages_focus_until_successful_settlement() -> None:
    """A failed scope switch leaves the prior Review checkpoint active."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    query = "Now investigate maize heat tolerance using a new source set."

    adapter.prepare(_projection(query), snapshot=snapshot, turn_id="scope-1")
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is not None
    assert adapter.staged_snapshot.research_question == query

    assert adapter.settle(False) == 4
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is None

    adapter.prepare(_projection(query), snapshot=snapshot, turn_id="scope-1")
    assert adapter.settle(True) == 5
    assert adapter.active_snapshot is not None
    assert adapter.active_snapshot.research_question == query


@pytest.mark.parametrize("answer", ["", "No answer generated."])
def test_capture_result_rejects_stale_report_for_invalid_answer(
    answer: str,
) -> None:
    """A stale report cannot make an invalid result stageable."""
    adapter = _follow_up_adapter("follow-up-4")

    adapter.capture_result(
        {
            "choices": [{"message": {"content": answer}}],
            "phytomni_state": {
                "summary_content": "A stale report from an earlier turn.",
                "original_user_query": "Review drought tolerance in rice",
                "report_revision": 4,
            },
        }
    )

    assert adapter.settlement_ready is False
    assert adapter.delta().summary_update is not None
    assert adapter.report_revision == 4


@pytest.mark.asyncio
async def test_load_review_checkpoint_uses_the_derived_thread_id() -> None:
    """Review checkpoint reads stay on the agent-private thread."""
    captured: dict[str, Any] = {}

    async def aget_state(config: dict[str, Any]) -> dict[str, Any]:
        """Capture the graph config and return the bounded checkpoint."""
        captured.update(config)
        return _checkpoint_state()

    snapshot = await load_review_checkpoint(
        _agent_with_app(SimpleNamespace(aget_state=aget_state)),
        _THREAD_ID,
    )

    assert snapshot is not None
    assert captured == {"configurable": {"thread_id": _THREAD_ID}}


@pytest.mark.asyncio
async def test_review_wrapper_answers_follow_up_without_running_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing Review turn uses the chat seam instead of ``arun``."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    projection = _projection("What evidence supports that claim?")
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, snapshot=snapshot, turn_id="follow-up-3")
    captured: dict[str, str] = {}

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return the active checkpoint for the follow-up chat probe."""
        return _checkpoint_state()

    async def _chat(prompt: str) -> dict[str, Any]:
        """Capture the bounded prompt and return a synthetic answer."""
        captured["prompt"] = prompt
        return {"choices": [{"message": {"content": "Bounded answer."}}]}

    async def chat(prompt: str) -> dict[str, Any]:
        """Expose the public chat seam used by the follow-up path."""
        return await _chat(prompt)

    async def arun(**_kwargs: Any) -> dict[str, Any]:
        """Fail if the follow-up path reruns the Review graph."""
        raise AssertionError("follow-up must not rerun the Review graph")

    fake_agent = SimpleNamespace(
        app=SimpleNamespace(aget_state=aget_state),
        _chat=_chat,
        chat=chat,
        arun=arun,
    )

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: fake_agent
    )
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=_THREAD_ID,
        review_adapter=adapter,
        review_projection=projection,
        review_turn_id="follow-up-3",
    )

    assert result["choices"][0]["message"]["content"] == "Bounded answer."
    assert _FULL_REPORT not in captured["prompt"]


@pytest.mark.asyncio
async def test_review_wrapper_marks_clarification_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-graph clarification must clear the prepared success flag."""
    projection = _projection("Review drought tolerance in rice", active=False)
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="clarification-1")
    captured: dict[str, Any] = {}

    async def arun(**kwargs: Any) -> dict[str, Any]:
        """Raise the graph clarification used by this failure-path probe."""
        captured.update(kwargs)
        raise ReviewClarificationError("graph clarification")

    fake_agent = SimpleNamespace(arun=arun)

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: fake_agent
    )
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=adapter.execution_thread_id,
        review_adapter=adapter,
        review_projection=projection,
        review_turn_id="clarification-1",
    )

    assert result["choices"][0]["message"]["content"] == "graph clarification"
    assert captured["auto_approve"] is True
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_revise_section_prompt_is_bounded_and_returns_section() -> None:
    """The focused seam receives no full report and returns one section."""
    captured: dict[str, str] = {}

    async def fake_chat(prompt: str) -> dict[str, Any]:
        captured["prompt"] = prompt
        return {
            "choices": [
                {"message": {"content": "Evidence claim is now qualified."}}
            ]
        }

    revised = await revise_section(
        section_id="evidence",
        section_text="Evidence claim needs a replication study.",
        instruction="State the limitation.",
        evidence_summary="Replication study remains unresolved.",
        chat=fake_chat,
    )

    assert revised == RevisedSection(
        section_id="evidence",
        heading="evidence",
        text="Evidence claim is now qualified.",
    )
    assert _FULL_REPORT not in captured["prompt"]
    assert "Evidence claim needs a replication study." in captured["prompt"]


def test_delta_keeps_report_bounded_and_controls_revision() -> None:
    """Context deltas stay bounded and artifact revision is success-only."""
    adapter = _follow_up_adapter("follow-up-5")
    delta = adapter.delta(
        {
            "result": {
                "formatted": {
                    "answer": (
                        "The evidence gap is replication, not absence "
                        "of evidence."
                    )
                }
            }
        }
    )

    payload = json.dumps(delta.model_dump(mode="json"), sort_keys=True)
    assert _FULL_REPORT not in payload
    assert "replication" in payload
    assert adapter.report_revision == 4
    adapter.capture_result(
        {
            "choices": [
                {
                    "message": {
                        "content": "A bounded evidence answer.",
                    }
                }
            ],
            "phytomni_state": {
                "original_user_query": "new scope",
                "report_revision": 99,
            },
        }
    )
    assert adapter.settle(False) == 4
    assert adapter.report_revision == 4
    assert adapter.settle(True) == 5
    assert adapter.report_revision == 5


@pytest.mark.asyncio
async def test_review_wrapper_forwards_private_thread_id_without_schema_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility wrapper passes stable thread IDs to ``arun``."""
    captured: dict[str, Any] = {}

    async def arun(**kwargs: Any) -> dict[str, Any]:
        """Capture wrapper kwargs and return a successful synthetic result."""
        captured.update(kwargs)
        return {"ok": True}

    fake_agent = SimpleNamespace(arun=arun)

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: fake_agent
    )
    result = await review_agent.review_agent_function(
        user_query="Review drought tolerance in rice",
        thread_id=_THREAD_ID,
    )

    assert result == {"ok": True}
    assert captured["thread_id"] == _THREAD_ID
    assert captured["user_query"] == "Review drought tolerance in rice"
    assert "auto_approve" not in captured


@pytest.mark.asyncio
async def test_executor_defers_review_checkpoint_until_explicit_ack(
    tmp_path: Any,
) -> None:
    """Staging never updates Review state before the durable ack seam."""
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": str(1),
            "request_id": "request-" + str(1),
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": "Review drought tolerance in rice",
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 1,
            "ledger_version": "a" * 64,
            "base_business_context_version": 0,
            "history_delta": [
                {
                    "turn_id": "1",
                    "role": "user",
                    "content": "Review drought tolerance in rice",
                }
            ],
            "artifact_refs": [],
        }
    )

    updates: list[dict[str, Any]] = []

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return the stable checkpoint used by the Review adapter."""
        return _checkpoint_state()

    async def aupdate_state(
        _config: dict[str, Any], *, values: dict[str, Any]
    ) -> None:
        """Record updates while keeping state changes explicit in the test."""
        updates.append(values)

    fake_agent = _agent_with_app(
        SimpleNamespace(
            updates=updates,
            aget_state=aget_state,
            aupdate_state=aupdate_state,
        )
    )
    captured: dict[str, Any] = {}
    store = ConversationContextStore(str(tmp_path / "context.sqlite"))

    async def invoke(
        _selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: Any,
    ) -> AgentOutcome:
        adapter = dispatch.private_agent_state["review_adapter"]
        candidate = adapter.candidate_thread_id
        assert candidate is not None
        with closed_sqlite_connection(store.db_path) as connection:
            assert connection.execute(
                "SELECT staged_at, eligible_at, tombstone_pending "
                "FROM conversation_review_checkpoint_cleanup "
                "WHERE conversation_key = ? AND candidate_thread_id = ?",
                (str(_CONVERSATION_KEY), candidate),
            ).fetchone() == (None, None, 0)
        await adapter.prepare_from_agent(
            dispatch.private_agent_state["review_projection"],
            fake_agent,
            dispatch.agent_thread_id,
            turn_id=dispatch.private_agent_state["review_turn_id"],
        )
        adapter.capture_result(
            {"choices": [{"message": {"content": "Review complete."}}]}
        )
        captured["adapter"] = adapter
        return AgentOutcome(
            result={"ok": True},
            context_delta=ContextDelta(),
            private_stage_metadata=adapter.settlement_metadata(),
        )

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("explicit Review selection must not route")

    executor = ConversationContextExecutor(
        store_factory=lambda: store,
        select_agent=forbidden_router,
    )
    prepared = await executor.execute(
        envelope=envelope,
        invoke=invoke,
        delegate_async=lambda *_args: pytest.fail(
            "Review must not delegate asynchronously"
        ),
    )

    assert prepared.status is PrepareStatus.RETURN_STAGED
    adapter = captured["adapter"]
    assert not fake_agent.app.updates
    assert adapter.report_revision == 4
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=False)
        is True
    )
    assert not fake_agent.app.updates
    assert adapter.report_revision == 4
    assert adapter.active_snapshot is not None
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )
    accepted_adapter = ReviewConversationAdapter()
    await accepted_adapter.prepare_from_agent(
        _projection("Review drought tolerance in rice"),
        fake_agent,
        _THREAD_ID,
        turn_id=envelope.turn_id,
    )
    accepted_adapter.capture_result(
        {"choices": [{"message": {"content": "Review complete."}}]}
    )
    accepted_envelope = envelope.model_copy(update={"turn_id": "2"})
    await executor.defer_review_settlement(accepted_envelope, accepted_adapter)
    assert not fake_agent.app.updates
    assert (
        await executor.acknowledge_review_settlement(
            accepted_envelope, accepted=True
        )
        is True
    )
    assert fake_agent.app.updates == [{"report_revision": 5}]
    assert accepted_adapter.report_revision == 5


@pytest.mark.asyncio
async def test_new_review_promotes_after_ack_and_discards_on_reject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Full-graph state stays on a candidate until durable acceptance."""
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": "7",
            "request_id": "request-7",
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": "Review maize heat tolerance",
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 7,
            "ledger_version": "b" * 64,
            "base_business_context_version": 0,
            "history_delta": [
                {
                    "turn_id": "7",
                    "role": "user",
                    "content": "Review maize heat tolerance",
                }
            ],
            "artifact_refs": [],
        }
    )
    stable_state = _checkpoint_state()
    candidate_state = _candidate_review_state(
        summary_content="# New report\n\nNew evidence.",
        report_revision=0,
    )

    fake_agent = _stateful_review_agent(stable_state, candidate_state)
    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: fake_agent
    )
    projection = _projection(
        "Start a new review of maize heat tolerance", active=False
    )
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id=envelope.turn_id)
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=adapter.execution_thread_id,
        review_adapter=adapter,
        review_projection=projection,
        review_turn_id=envelope.turn_id,
    )

    assert result["choices"][0]["message"]["content"] == (
        "Current public answer."
    )
    assert fake_agent.graph_threads == [adapter.candidate_thread_id]
    assert adapter.candidate_thread_id != adapter.stable_thread_id
    stable_before_ack = dict(fake_agent.app.states[_THREAD_ID])

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(
            str(tmp_path / "context.sqlite")
        ),
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "the direct settlement test must not route"
        ),
    )
    await executor.defer_review_settlement(envelope, adapter)
    assert not fake_agent.app.updates
    assert fake_agent.app.states[_THREAD_ID] == stable_before_ack
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is True
    )
    assert fake_agent.app.updates[0][0] == _THREAD_ID
    assert fake_agent.app.states[_THREAD_ID]["summary_content"] == (
        "# New report\n\nNew evidence."
    )
    assert fake_agent.app.states[_THREAD_ID]["report_revision"] == 5
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )

    rejected_envelope = envelope.model_copy(update={"turn_id": "8"})
    rejected_projection = _projection(
        "Start a new review of barley heat tolerance", active=False
    )
    rejected_adapter = ReviewConversationAdapter()
    rejected_adapter.prepare(rejected_projection, turn_id="8")
    await review_agent.review_agent_function(
        user_query=rejected_projection.current_query,
        thread_id=rejected_adapter.execution_thread_id,
        review_adapter=rejected_adapter,
        review_projection=rejected_projection,
        review_turn_id="8",
    )
    rejected_candidate = rejected_adapter.candidate_thread_id
    assert rejected_candidate is not None
    await executor.defer_review_settlement(rejected_envelope, rejected_adapter)
    assert (
        await executor.acknowledge_review_settlement(
            rejected_envelope, accepted=False
        )
        is True
    )
    assert fake_agent.app.states[_THREAD_ID]["report_revision"] == 5
    assert rejected_candidate in fake_agent.app.deleted


@pytest.mark.asyncio
async def test_failed_review_ack_discards_candidate_without_stable_advance(
    tmp_path: Any,
) -> None:
    """A lost promotion acknowledgement cannot advance the checkpoint."""
    stable_state = _checkpoint_state()
    candidate_state = _candidate_review_state()

    class FailingApp:
        """Graph-app double that fails during durable promotion."""

        def __init__(self) -> None:
            self.states = {_THREAD_ID: dict(stable_state)}
            self.updates: list[str] = []
            self.deleted: list[str] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            """Read stable or candidate state for the failure-path probe."""
            thread_id = config["configurable"]["thread_id"]
            return self.states.get(thread_id, candidate_state)

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            """Record the attempted update, then raise the synthetic outage."""
            self.updates.append(config["configurable"]["thread_id"])
            del values
            raise RuntimeError("ledger acknowledgement lost")

        async def adelete_thread(self, thread_id: str) -> None:
            """Record candidate cleanup requests from the failed ack path."""
            self.deleted.append(thread_id)

    projection = _projection(
        "Start a new review of maize heat tolerance", active=True
    )
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="9")
    failing_app = FailingApp()
    agent = _agent_with_app(failing_app)
    await adapter.prepare_from_agent(
        projection, agent, _THREAD_ID, turn_id="9"
    )
    adapter.capture_result(
        {
            "choices": [{"message": {"content": "Candidate answer."}}],
            "phytomni_state": candidate_state,
        }
    )
    stable_before_ack = dict(agent.app.states[_THREAD_ID])

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(
            str(tmp_path / "context.sqlite")
        ),
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "the direct settlement test must not route"
        ),
    )
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": "9",
            "request_id": "request-9",
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": projection.current_query,
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 9,
            "ledger_version": "c" * 64,
            "base_business_context_version": 0,
            "history_delta": [],
            "artifact_refs": [],
        }
    )
    await executor.defer_review_settlement(envelope, adapter)

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await executor.acknowledge_review_settlement(envelope, accepted=True)

    assert agent.app.states[_THREAD_ID] == stable_before_ack
    assert agent.app.updates == [_THREAD_ID]
    assert adapter.report_revision == 4
    assert adapter.settlement_ready is False
    assert adapter.candidate_thread_id in agent.app.deleted
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )
