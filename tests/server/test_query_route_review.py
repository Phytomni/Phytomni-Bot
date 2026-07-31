# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review context follow-up and candidate-graph route tests."""

from __future__ import annotations

import sqlite3

from tests.server.test_query_route import (
    _REVIEW_REPORT,
    UUID,
    Any,
    ConversationContextStore,
    Path,
    SimpleNamespace,
    _conversation_envelope,
    _patch_review_runtime,
    _post_context_route,
    _post_query_route,
    _review_checkpoint_state,
    _review_context_envelope,
    _ReviewFakeAgent,
    _ReviewFakeApp,
    api_app,
    httpx,
    json,
    pytest,
    review_agent,
)

from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id as context_agent_thread_id,
)

pytestmark = pytest.mark.server


@pytest.mark.asyncio
async def test_context_expert_store_failure_returns_retryable_503(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expert context storage failure stops before agent dispatch."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))

    def fail_begin(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("private storage detail")

    monkeypatch.setattr(ConversationContextStore, "begin_turn", fail_begin)
    envelope = _conversation_envelope(
        requested_agent_id="DataAgent",
        allowed_agent_ids=["DataAgent"],
    )
    response = await _post_context_route(
        api_client, issued_api_key, "DataAgent", envelope
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "conversation_context_unavailable"
    assert error["stage"] == "context"
    assert error["retryable"] is True
    assert "private storage detail" not in response.text


@pytest.mark.parametrize(
    "case",
    [
        (
            "What new evidence supports that claim?",
            "Bounded evidence answer.",
        ),
        (
            "Review the new evidence supporting that claim.",
            "Bounded evidence answer.",
        ),
        (
            "Rewrite the Evidence section to state the limitation.",
            _REVIEW_REPORT.replace(
                "Evidence claim [document:2].",
                "Evidence claim is qualified.",
            ),
        ),
    ],
)
async def test_context_expert_review_follow_up_and_revision_use_adapter(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: tuple[str, str],
) -> None:
    """V1 Review follow-ups and edits bypass the A2UI full graph."""
    query = case[0]
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    def local_response(_prompt: str) -> dict[str, Any]:
        """Return the answer expected for this parametrized query."""
        content = (
            "Bounded evidence answer."
            if "new evidence" in query
            else "Evidence claim is qualified."
        )
        return {"choices": [{"message": {"content": content}}]}

    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(_review_checkpoint_state()),
        SimpleNamespace(
            chat_response=local_response,
            run_error=AssertionError(
                "Review follow-up and local revision reran graph"
            ),
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)

    async def forbidden_review_graph(**_kwargs: Any) -> Any:
        raise AssertionError("V1 Review must not enter the A2UI graph")

    monkeypatch.setattr(
        api_app, "_run_review_with_interrupt", forbidden_review_graph
    )
    envelope = _review_context_envelope(query)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_context"]["selected_agent_id"] == (
        "ReviewAgent"
    )
    assert response.json()["result"]["formatted"]["answer"] == case[1]
    assert fake_agent.graph_calls == 0
    assert fake_agent.chat_prompts
    assert _REVIEW_REPORT not in fake_agent.chat_prompts[0]
    expected_thread = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "ReviewAgent"
    )
    assert fake_agent.app.state_reads == [
        {"configurable": {"thread_id": expected_thread}},
        {"configurable": {"thread_id": expected_thread}},
    ]
    assert not fake_agent.app.updates
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.delta is not None
    assert _REVIEW_REPORT not in json.dumps(staged.delta)


@pytest.mark.parametrize("scope_change", [False, True])
@pytest.mark.asyncio
async def test_context_expert_review_full_graph_uses_candidate_thread(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope_change: bool,
) -> None:
    """V1 new and scope Review graphs cannot write the stable checkpoint."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = (
        "Start a new review of maize heat tolerance."
        if scope_change
        else "Review maize heat tolerance."
    )
    if scope_change:
        envelope = _review_context_envelope(query, turn_id="7")
        stable_checkpoint: dict[str, Any] = _review_checkpoint_state()
    else:
        envelope = _conversation_envelope(
            turn_id="7",
            requested_agent_id="ReviewAgent",
            allowed_agent_ids=["ReviewAgent"],
        )
        envelope["current_message"]["content"] = query
        envelope["history_delta"] = [
            {"turn_id": "7", "role": "user", "content": query}
        ]
        stable_checkpoint = {}

    run_state = {
        "original_user_query": query,
        "summary_content": "# Candidate report\n\nCandidate evidence.",
        "research_dimensions": ["Evidence"],
        "report_artifact_id": "report-1",
        "report_revision": 0,
    }
    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(stable_checkpoint),
        SimpleNamespace(
            run_response={
                "choices": [
                    {"message": {"content": "Candidate public answer."}}
                ],
                "phytomni_state": run_state,
            },
            run_state=run_state,
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review full graph must use the native candidate path"
        ),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["formatted"]["answer"] == (
        "Candidate public answer."
    )
    stable_thread = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "ReviewAgent"
    )
    assert len(fake_agent.graph_threads) == 1
    candidate_thread = fake_agent.graph_threads[0]
    assert candidate_thread is not None
    assert candidate_thread != stable_thread
    assert candidate_thread.startswith(f"{stable_thread}:candidate:")
    assert fake_agent.app.state_reads == [
        {"configurable": {"thread_id": stable_thread}},
        {"configurable": {"thread_id": candidate_thread}},
    ]
    assert not fake_agent.app.updates
    assert (
        fake_agent.app.states.get(stable_thread, stable_checkpoint)
        == stable_checkpoint
    )
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "staged"
    assert staged.delta is not None
    assert staged.stage_metadata is not None
    assert (
        staged.stage_metadata["_review_settlement"]["candidate_thread_id"]
        == candidate_thread
    )
    assert "_review_settlement" not in response.text


@pytest.mark.asyncio
async def test_context_expert_review_missing_candidate_fails_before_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A graph answer is not healthy until its candidate checkpoint exists."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = "Review maize heat tolerance."
    envelope = _conversation_envelope(
        turn_id="11",
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {"turn_id": "11", "role": "user", "content": query}
    ]

    fake_agent = _ReviewFakeAgent(_ReviewFakeApp())
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must use the native graph invocation seam"
        ),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 1
    assert not fake_agent.app.updates
    stored = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert stored is not None
    assert stored.state == "failed"
    assert stored.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_graph_clarification_fails_without_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A full-graph Review clarification cannot become a healthy stage."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = "Review maize heat tolerance."
    envelope = _conversation_envelope(
        turn_id="8",
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {"turn_id": "8", "role": "user", "content": query}
    ]

    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(),
        SimpleNamespace(
            run_error=review_agent.ReviewClarificationError(
                "graph clarification"
            )
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must use the native graph invocation seam"
        ),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 1
    assert not fake_agent.app.updates
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_empty_local_revision_does_not_settle(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty section response leaves the Review revision unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(_review_checkpoint_state()),
        SimpleNamespace(
            chat_response={"choices": [{"message": {"content": ""}}]},
            run_error=AssertionError(
                "empty local revision must not rerun graph"
            ),
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)

    async def forbidden_review_graph(**_kwargs: Any) -> Any:
        raise AssertionError("V1 Review must not enter the A2UI graph")

    monkeypatch.setattr(
        api_app, "_run_review_with_interrupt", forbidden_review_graph
    )
    envelope = _review_context_envelope(
        "Rewrite the Evidence section to state the limitation."
    )
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 0
    assert not fake_agent.app.updates
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None
    assert "Review section revision completed." not in json.dumps(staged.delta)


@pytest.mark.asyncio
async def test_context_expert_review_empty_follow_up_fails_without_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty Review follow-up is failed and cannot settle context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(_review_checkpoint_state()),
        SimpleNamespace(
            chat_response={"choices": [{"message": {"content": ""}}]},
            run_error=AssertionError("empty follow-up must not rerun graph"),
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must not enter the full graph"
        ),
    )
    envelope = _review_context_envelope(
        "What new evidence supports that claim?", turn_id="4"
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert not fake_agent.app.updates
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_unknown_section_clarification_fails_turn(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unknown local section is clarification, never a healthy stage."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    fake_agent = _ReviewFakeAgent(
        _ReviewFakeApp(_review_checkpoint_state()),
        SimpleNamespace(
            chat_error=AssertionError(
                "unknown section must clarify before chat"
            ),
            run_error=AssertionError("unknown section must not rerun graph"),
        ),
    )
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must not enter the full graph"
        ),
    )
    envelope = _review_context_envelope(
        "Rewrite the Methods section to be shorter.", turn_id="5"
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert not fake_agent.app.updates
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None
