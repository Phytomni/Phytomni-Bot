# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Knowledge and Brief Gene context route tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.server.test_query_route import (
    UUID,
    Any,
    ConversationContextStore,
    Path,
    SimpleNamespace,
    ToolSelection,
    _conversation_envelope,
    _context_follow_up_envelope,
    _patch_knowledge_runtime,
    _patch_handler_runtime,
    _post_context_route,
    _post_query_route,
    _success_agent_body,
    api_app,
    httpx,
    json,
    mcp_handlers,
    pytest,
    server,
)

from mcp_server_phytomni.agents.brief_gene import agent as brief_gene_agent
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id as context_agent_thread_id,
)

pytestmark = pytest.mark.server


async def test_context_expert_knowledge_turn_separates_retrieval_context(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Knowledge resolves retrieval privately and stages bounded context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    answer_marker = "KNOWLEDGE_ROUTE_ANSWER_OUTPUT_SENTINEL"
    calls: list[dict[str, Any]] = []

    async def fake_knowledge_arun(**kwargs: Any) -> dict[str, Any]:
        """Capture Knowledge arguments and return a bounded answer."""
        calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": answer_marker,
                        "doc_list": [
                            {
                                "file_id": "doc-1",
                                "title": "Paper 1.pdf",
                                "content": (
                                    "full report body that must " "not persist"
                                ),
                            }
                        ],
                        "follow_up_questions": [
                            "What promoter evidence exists for OsDREB1?"
                        ],
                    }
                }
            ]
        }

    _patch_knowledge_runtime(
        monkeypatch,
        SimpleNamespace(calls=calls, arun=fake_knowledge_arun),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.citation_enrichment.bi_query",
        AsyncMock(return_value={"message": "ok", "data": []}),
    )
    envelope = _conversation_envelope(
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    envelope["turn_id"] = "3"
    envelope["request_id"] = "request-3"
    envelope["ledger_cursor"] = 3
    envelope["current_message"]["content"] = "What evidence supports that?"
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about rice gene OsDREB1.",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "OsDREB1 improves drought tolerance [1].",
            "summary": "OsDREB1 improves drought tolerance [1].",
        },
        {
            "turn_id": "3",
            "role": "user",
            "content": "What evidence supports that?",
        },
    ]

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_context"]["selected_agent_id"] == (
        "KnowledgeAgent"
    )
    assert response.json()["result"]["formatted"]["answer"] == answer_marker
    assert calls == [
        {
            "user_query": "What evidence supports that?",
            "obs_file_list": [],
            "repo_id_dict": None,
            "is_generate": True,
            "is_follow_up": True,
            "locale": "en-US",
            "retrieval_query": "What evidence supports OsDREB1?",
            "answer_context": (
                "[recent turn 1]\nuser: Tell me about rice gene OsDREB1.\n\n"
                "[recent turn 2]\nassistant: OsDREB1 improves drought "
                "tolerance [1]."
            ),
            "thread_id": context_agent_thread_id(
                UUID(envelope["conversation_key"]), "KnowledgeAgent"
            ),
            "conversation_messages": (),
        }
    ]
    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "3")
    assert staged is not None
    assert staged.delta is not None
    assert [item["label"] for item in staged.delta["active_entities"]] == [
        "OsDREB1"
    ]
    assert answer_marker not in json.dumps(staged.delta, sort_keys=True)
    assert answer_marker in json.dumps(staged.result, sort_keys=True)
    assert "full report body" not in json.dumps(staged.delta, sort_keys=True)
    assert staged.stage_metadata is not None
    assert staged.stage_metadata["selected_agent_id"] == "KnowledgeAgent"
    assert staged.stage_metadata["route_source"] == "explicit_selection"

    assert (
        store.commit_staged_turn(
            str(UUID(envelope["conversation_key"])),
            envelope["turn_id"],
            envelope["ledger_version"],
            envelope["ledger_version"],
        ).state
        == "committed"
    )
    stored_context = store.load_context(
        str(UUID(envelope["conversation_key"]))
    )
    assert stored_context is not None
    assert answer_marker not in json.dumps(
        stored_context.context, sort_keys=True
    )
    assert stored_context.context["active_entities"]

    replay_response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )
    assert replay_response.status_code == 200
    assert (
        replay_response.json()["result"]["formatted"]["answer"]
        == answer_marker
    )
    replayed_context = store.load_context(
        str(UUID(envelope["conversation_key"]))
    )
    assert replayed_context is not None
    assert answer_marker not in json.dumps(
        replayed_context.context, sort_keys=True
    )


async def test_context_expert_knowledge_follow_up_returns_clarification(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unresolved Knowledge pronoun asks for clarification."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    calls: list[dict[str, Any]] = []

    async def fake_knowledge_arun(**kwargs: Any) -> dict[str, Any]:
        """Capture an invocation that should be skipped by clarification."""
        calls.append(kwargs)
        return {"choices": [{"message": {"content": "unexpected"}}]}

    _patch_knowledge_runtime(
        monkeypatch,
        SimpleNamespace(arun=fake_knowledge_arun),
    )
    envelope = _conversation_envelope(
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    envelope["turn_id"] = "4"
    envelope["request_id"] = "request-4"
    envelope["ledger_cursor"] = 4
    envelope["current_message"]["content"] = "What evidence supports that?"
    envelope["history_delta"] = [
        {
            "turn_id": "4",
            "role": "user",
            "content": "What evidence supports that?",
        }
    ]

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "clarify" in body["result"]["formatted"]["answer"].lower()
    assert not calls
    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "4")
    assert staged is not None
    assert staged.delta is not None
    assert staged.delta["active_entities"] == []


async def test_context_expert_brief_gene_turn_stages_bounded_context_delta(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The native Brief Gene route returns its bounded context projection."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    calls: list[dict[str, Any]] = []

    async def fake_brief_gene_arun(**kwargs: Any) -> dict[str, Any]:
        """Capture Brief Gene arguments and return a bounded report."""
        calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "# Brief Gene Analysis\n\n"
                            "Rice gene summary [1]."
                        ),
                        "doc_list": [
                            {
                                "file_id": "paper-1",
                                "title": "Paper 1",
                                "content": "full report body",
                            }
                        ],
                    }
                }
            ],
            "phytomni_state": {
                "gene_id": "Os01g0177400",
                "species_code": "osa",
                "report_summary": "Bounded rice gene summary.",
                "report_artifact_id": "brief-report-1",
                "report_revision": 4,
                "retrieved_docs": [
                    {
                        "file_id": "paper-1",
                        "content": "full report body",
                    }
                ],
            },
        }

    monkeypatch.setattr(
        brief_gene_agent,
        "resolve_brief_gene_user_query",
        AsyncMock(
            return_value=SimpleNamespace(
                gene_id="Os01g0177400", species_code="osa"
            )
        ),
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: SimpleNamespace(
            calls=calls, arun=fake_brief_gene_arun
        ),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "BriefGeneConfig",
        lambda: SimpleNamespace(MAX_CONCURRENCY=1),
    )
    _patch_handler_runtime(monkeypatch)
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.citation_enrichment.bi_query",
        AsyncMock(return_value={"message": "ok", "data": []}),
    )

    envelope = _conversation_envelope(
        turn_id="6",
        requested_agent_id="BriefGeneAgent",
        allowed_agent_ids=["BriefGeneAgent"],
    )
    envelope["ledger_cursor"] = 6
    envelope["current_message"]["content"] = "Os01g0177400"
    envelope["history_delta"] = [
        {
            "turn_id": "6",
            "role": "user",
            "content": "Os01g0177400",
        }
    ]
    envelope["artifact_refs"] = [
        {
            "artifact_id": "brief-report-1",
            "display_name": "Brief Gene report",
        }
    ]

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["BriefGeneAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_context"]["selected_agent_id"] == (
        "BriefGeneAgent"
    )
    assert response.json()["conversation_context"]["context_degraded"] is False
    assert response.json()["result"]["formatted"]["answer"] == (
        "# Brief Gene Analysis\n\nRice gene summary [1]."
    )
    assert calls == [
        {
            "user_query": "Os01g0177400",
            "locale": "en-US",
            "thread_id": context_agent_thread_id(
                UUID(envelope["conversation_key"]), "BriefGeneAgent"
            ),
        }
    ]

    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "6")
    assert staged is not None
    assert staged.delta is not None
    labels = {item["label"] for item in staged.delta["active_entities"]}
    assert {
        "Os01g0177400",
        "osa",
        "paper-1",
        "brief-report-1",
        "report revision 4",
    } <= labels
    entity_ids = {
        item["entity_id"] for item in staged.delta["active_entities"]
    }
    assert {
        "brief_gene.gene.os01g0177400",
        "brief_gene.species.osa",
        "brief_gene.evidence.paper-1",
        "brief_gene.report_revision.4",
        "brief_gene.artifact.brief-report-1",
    } <= entity_ids
    assert all(":" not in entity_id for entity_id in entity_ids)
    assert staged.delta["task_summary"] == "Bounded rice gene summary."
    assert [
        item["artifact_id"] for item in staged.delta["artifact_index"]
    ] == ["brief-report-1"]
    assert "full report body" not in json.dumps(staged.delta)


async def test_context_expert_knowledge_switch_replaces_topic(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful topic switch commits only the new Knowledge topic."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Knowledge selection must not route")

    captured: list[dict[str, Any]] = []

    async def fake_invoke(
        *,
        agent: str,
        arguments: dict[str, Any],
        private_agent_state: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        captured.append(
            {
                "agent": agent,
                "arguments": dict(arguments),
                "private_agent_state": dict(private_agent_state or {}),
            }
        )
        query = arguments["user_query"]
        if query == "Tell me about OsDREB1 drought evidence.":
            return (
                _success_agent_body(
                    agent,
                    "OsDREB1 improves drought tolerance [1].",
                ),
                200,
            )
        if query == "Tell me about OsNAC6 drought evidence.":
            return (
                _success_agent_body(
                    agent,
                    "OsNAC6 improves drought tolerance [2].",
                ),
                200,
            )
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    conversation_key = str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"))
    store = ConversationContextStore(str(db_path))

    first = _conversation_envelope(
        turn_id="1",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    first["current_message"][
        "content"
    ] = "Tell me about OsDREB1 drought evidence."
    first["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about OsDREB1 drought evidence.",
        }
    ]

    assert (
        await _post_context_route(
            api_client, issued_api_key, "KnowledgeAgent", first
        )
    ).status_code == 200
    first_staged = store.load_turn(conversation_key, "1")
    assert first_staged is not None
    assert first_staged.delta is not None
    assert [
        item["label"] for item in first_staged.delta["active_entities"]
    ] == ["OsDREB1"]
    store.commit_staged_turn(
        conversation_key,
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _context_follow_up_envelope(
        "KnowledgeAgent", "Tell me about OsNAC6 drought evidence."
    )

    assert (
        await _post_context_route(
            api_client, issued_api_key, "KnowledgeAgent", second
        )
    ).status_code == 200
    second_staged = store.load_turn(conversation_key, "2")
    assert second_staged is not None
    assert second_staged.delta is not None
    assert [
        item["label"] for item in second_staged.delta["active_entities"]
    ] == ["OsNAC6"]
    committed = store.commit_staged_turn(
        conversation_key,
        "2",
        second["ledger_version"],
        "d" * 64,
    )
    assert [
        item["label"] for item in committed.context.context["active_entities"]
    ] == ["OsNAC6"]
    assert captured[1]["private_agent_state"]["retrieval_query"] == (
        "Tell me about OsNAC6 drought evidence."
    )


async def test_context_expert_knowledge_failed_switch_keeps_prior_topic(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed explicit topic switch leaves the committed topic unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Knowledge selection must not route")

    async def fake_invoke(
        *,
        agent: str,
        arguments: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        if (
            arguments["user_query"]
            == "Tell me about OsDREB1 drought evidence."
        ):
            return (
                _success_agent_body(
                    agent, "OsDREB1 improves drought tolerance [1]."
                ),
                200,
            )
        return (
            {
                "id": f"{agent}-run",
                "object": "agent.run",
                "agent": agent,
                "status": "running",
                "task_ids": [],
                "result": {"formatted": {}},
            },
            200,
        )

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    conversation_key = str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"))
    store = ConversationContextStore(str(db_path))

    first = _conversation_envelope(
        turn_id="1",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    first["current_message"][
        "content"
    ] = "Tell me about OsDREB1 drought evidence."
    first["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about OsDREB1 drought evidence.",
        }
    ]

    first_response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": first,
        },
    )

    assert first_response.status_code == 200
    store.commit_staged_turn(
        conversation_key,
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _conversation_envelope(
        turn_id="2",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
        base_business_context_version=1,
    )
    second["ledger_cursor"] = 2
    second["ledger_version"] = "c" * 64
    second["current_message"][
        "content"
    ] = "Tell me about OsNAC6 drought evidence."
    second["history_delta"] = [
        {
            "turn_id": "2",
            "role": "user",
            "content": "Tell me about OsNAC6 drought evidence.",
        }
    ]

    second_response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": second,
        },
    )

    assert second_response.status_code == 409
    assert (
        second_response.json()["error"]["code"]
        == "conversation_context_turn_in_progress"
    )
    stored_context = store.load_context(conversation_key)
    assert stored_context is not None
    assert [
        item["label"] for item in stored_context.context["active_entities"]
    ] == ["OsDREB1"]
    failed_turn = store.load_turn(conversation_key, "2")
    assert failed_turn is not None
    assert failed_turn.state == "failed"


async def test_context_expert_rebuilds_before_routing_when_state_is_missing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale V1 base returns rebuild-required before selecting an agent."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError(
            "missing context must request rebuild before routing"
        )

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query",
            "allowed_tools": ["DataAgent"],
            "conversation": _conversation_envelope(
                allowed_agent_ids=["DataAgent"],
                base_business_context_version=1,
            ),
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"]
        == "conversation_context_rebuild_required"
    )


async def test_context_expert_rejects_selected_agent_outside_allowlist(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A V1 selector cannot dispatch a tool outside Go's ordered allowlist."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    invoked = 0

    async def select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        return ToolSelection("DataAgent", {"user_query": "forbidden"})

    async def forbidden(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("outside selection must not dispatch")

    monkeypatch.setattr(api_app, "select_agent_tool", select)
    monkeypatch.setitem(
        server.TOOL_HANDLERS, server.PhytomniAgents.DATA_AGENT.value, forbidden
    )
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query",
            "allowed_tools": ["ChatAgent"],
            "conversation": _conversation_envelope(
                allowed_agent_ids=["ChatAgent"]
            ),
        },
    )

    assert response.status_code == 502
    assert invoked == 0
