# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for Brief Gene conversation operation separation."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.brief_gene import agent as brief_gene_agent
from mcp_server_phytomni.agents.brief_gene.conversation import (
    BriefGeneClarificationError,
    BriefGeneConversationAdapter,
    BriefGeneConversationOperation,
    classify_brief_gene_operation,
)
from mcp_server_phytomni.agents.brief_gene.resolve_query import (
    BriefGeneIdCandidate,
    BriefGeneResolveError,
    BriefGeneResolveResult,
)
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.mcp.app import invoke_tool_raw
from mcp_server_phytomni.mcp.formatting.cited import (
    format_cited_message_result,
)
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    brief_gene_agent_invocation,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ArtifactRefV1,
    ContextDelta,
    ContextEntity,
    ContextProjection,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)

pytestmark = pytest.mark.agent

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_THREAD_ID = agent_thread_id(_CONVERSATION_KEY, "BriefGeneAgent")


def _projection(
    query: str,
    *,
    active: bool = False,
    species: str = "osa",
    artifact_id: str | None = "brief-report-1",
) -> ContextProjection:
    """Build one bounded Brief Gene projection."""
    entities: list[ContextEntity] = []
    artifacts: list[ArtifactRefV1] = []
    if active:
        entities.extend(
            [
                ContextEntity(
                    entity_id="brief_gene.gene.os01g0177400",
                    entity_type="gene",
                    label="Os01g0177400",
                ),
                ContextEntity(
                    entity_id=f"brief_gene.species.{species}",
                    entity_type="species",
                    label=species,
                ),
                ContextEntity(
                    entity_id="brief_gene.evidence.paper-1",
                    entity_type="task",
                    label="paper-1",
                ),
                ContextEntity(
                    entity_id="brief_gene.report_revision.3",
                    entity_type="task",
                    label="report revision 3",
                ),
            ]
        )
    if artifact_id is not None:
        entities.append(
            ContextEntity(
                entity_id=f"brief_gene.artifact.{artifact_id}",
                entity_type="file",
                label=artifact_id,
            )
        )
        artifacts.append(
            ArtifactRefV1(
                artifact_id=artifact_id,
                display_name="Brief Gene report",
            )
        )
    return ContextProjection(
        current_query=query,
        task_summary=(
            "Os01g0177400 is an active rice gene report." if active else ""
        ),
        active_entities=entities,
        artifact_refs=artifacts,
        agent_thread_id=_THREAD_ID,
        locale="en-US",
        token_budget=2048,
    )


def _full_result(
    *,
    gene_id: str = "Os01g0177400",
    species_code: str = "osa",
    artifact_id: str = "brief-report-1",
    revision: int = 4,
) -> dict[str, Any]:
    """Build a native Brief Gene result with bounded metadata sources."""
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        "# Brief Gene Analysis\n\n" "Rice gene report [1]."
                    ),
                    "doc_list": [{"source_id": "paper-1", "title": "Paper"}],
                    "total": 10000,
                }
            }
        ],
        "phytomni_state": {
            "gene_id": gene_id,
            "species_code": species_code,
            "report_artifact_id": artifact_id,
            "report_revision": revision,
            "retrieved_docs": [
                {"source_id": "paper-1", "title": "Paper", "content": "body"}
            ],
        },
    }


def _resolved(gene_id: str = "Os01g0177400") -> BriefGeneResolveResult:
    """Build one deterministic resolver result."""
    return BriefGeneResolveResult(
        gene_id=gene_id,
        species_code=(
            "osa" if gene_id.lower().startswith(("os", "loc_os")) else "ath"
        ),
        raw_query=gene_id,
        candidates=[
            BriefGeneIdCandidate(
                gene_id=gene_id,
                species_code=(
                    "osa"
                    if gene_id.lower().startswith(("os", "loc_os"))
                    else "ath"
                ),
                confidence=1.0,
            )
        ],
    )


def test_classification_keeps_new_evidence_as_follow_up() -> None:
    """Natural-language evidence questions do not trigger a new report."""
    projection = _projection("Where is it expressed?", active=True)
    assert (
        classify_brief_gene_operation(projection)
        is BriefGeneConversationOperation.FOLLOW_UP
    )
    assert (
        classify_brief_gene_operation(
            "Review the new evidence supporting that claim",
            active_gene_id="Os01g0177400",
        )
        is BriefGeneConversationOperation.FOLLOW_UP
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Arabidopsis expression?", BriefGeneConversationOperation.CLARIFY),
        ("rice expression?", BriefGeneConversationOperation.FOLLOW_UP),
    ],
)
def test_species_only_queries_respect_active_species(
    query: str,
    expected: BriefGeneConversationOperation,
) -> None:
    """Species-only conflicts clarify without changing ordinary follow-ups."""
    assert (
        classify_brief_gene_operation(
            query,
            active_gene_id="Os01g0177400",
            active_species_code="osa",
        )
        is expected
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("refresh the report", BriefGeneConversationOperation.REFRESH),
        ("rerun the latest report", BriefGeneConversationOperation.REFRESH),
        ("Os01g0177400", BriefGeneConversationOperation.NEW_REPORT),
    ],
)
def test_classification_distinguishes_refresh_and_new_report(
    query: str,
    expected: BriefGeneConversationOperation,
) -> None:
    """Explicit refresh and first-turn identifiers have distinct operations."""
    active_gene = (
        "Os01g0177400"
        if expected is not BriefGeneConversationOperation.NEW_REPORT
        else None
    )
    assert (
        classify_brief_gene_operation(query, active_gene_id=active_gene)
        is expected
    )


def test_conflicting_identifier_or_species_clarifies() -> None:
    """Ambiguous identifiers never reach the resolver or full graph."""
    assert classify_brief_gene_operation("Os01g0177400 and AT1G01010") is (
        BriefGeneConversationOperation.CLARIFY
    )
    assert (
        classify_brief_gene_operation(
            "rice AT1G01010",
            active_gene_id="Os01g0177400",
            active_species_code="osa",
        )
        is BriefGeneConversationOperation.CLARIFY
    )
    assert (
        classify_brief_gene_operation(
            "analyze a new gene", active_gene_id="Os01g0177400"
        )
        is BriefGeneConversationOperation.CLARIFY
    )
    assert (
        classify_brief_gene_operation(
            "generate a new report for AT1G01010",
            active_gene_id="Os01g0177400",
            active_species_code="osa",
        )
        is BriefGeneConversationOperation.NEW_IDENTIFIER
    )
    assert (
        classify_brief_gene_operation(
            "Arabidopsis AT1G01010",
            active_gene_id="Os01g0177400",
            active_species_code="osa",
        )
        is BriefGeneConversationOperation.NEW_IDENTIFIER
    )


@pytest.mark.asyncio
async def test_first_gene_runs_resolver_and_full_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first valid identifier resolves and invokes the full agent."""
    adapter = BriefGeneConversationAdapter()
    projection = _projection("Os01g0177400")
    prepared = adapter.prepare(projection)
    assert prepared["operation"] is BriefGeneConversationOperation.NEW_REPORT

    calls: dict[str, Any] = {}

    async def resolve(query: str, **_kwargs: Any) -> BriefGeneResolveResult:
        calls["resolver_query"] = query
        return _resolved(query)

    async def arun(**kwargs: Any) -> dict[str, Any]:
        """Record the full-workflow invocation and return its fixture."""
        calls["arun"] = kwargs
        return _full_result()

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", resolve
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *args, **kwargs: SimpleNamespace(arun=arun),
    )

    result = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert result["choices"][0]["message"]["content"]
    assert calls["resolver_query"] == "Os01g0177400"
    assert calls["arun"]["user_query"] == "Os01g0177400"
    assert calls["arun"]["thread_id"] == _THREAD_ID
    assert adapter.active_gene_id == "Os01g0177400"


@pytest.mark.asyncio
async def test_follow_up_uses_active_context_without_resolver_or_full_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversational question uses the active bounded report context."""
    projection = _projection("Where is it expressed?", active=True)
    adapter = BriefGeneConversationAdapter()
    prepared = adapter.prepare(projection)
    assert prepared["operation"] is BriefGeneConversationOperation.FOLLOW_UP
    calls: dict[str, Any] = {}

    async def fail_resolve(*_args: Any, **_kwargs: Any) -> Any:
        calls["resolver"] = True
        raise AssertionError(
            "follow-up must not resolve the natural-language query"
        )

    async def fail_full(*_args: Any, **_kwargs: Any) -> Any:
        calls["full"] = True
        raise AssertionError("follow-up must not invoke the full graph")

    async def chat(prompt: str, **_kwargs: Any) -> dict[str, Any]:
        calls["prompt"] = prompt
        return {
            "choices": [
                {"message": {"content": "It is expressed in leaves [1]."}}
            ]
        }

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", fail_resolve
    )
    monkeypatch.setattr(brief_gene_agent, "get_cached_agent", fail_full)
    monkeypatch.setattr(brief_gene_agent, "invoke_brief_gene_chat", chat)

    result = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert "Os01g0177400" in calls["prompt"]
    assert "paper-1" in calls["prompt"]
    assert calls.get("resolver") is None
    assert calls.get("full") is None
    assert result["choices"][0]["message"]["content"] == (
        "It is expressed in leaves [1]."
    )
    assert result["choices"][0]["message"]["doc_list"] == [
        {"file_id": "paper-1"}
    ]
    formatted = format_cited_message_result(result)
    assert formatted.references[0]["file_id"] == "paper-1"


@pytest.mark.asyncio
async def test_refresh_runs_full_workflow_for_active_gene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh reuses the active identifier but rebuilds the report."""
    projection = _projection("refresh the report", active=True)
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)
    calls: dict[str, Any] = {}

    async def resolve(query: str, **_kwargs: Any) -> BriefGeneResolveResult:
        calls["resolver_query"] = query
        return _resolved(query)

    async def arun(**kwargs: Any) -> dict[str, Any]:
        """Record the refresh invocation and return its fixture."""
        calls["arun"] = kwargs
        return _full_result(revision=5)

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", resolve
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *args, **kwargs: SimpleNamespace(arun=arun),
    )

    await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert calls["resolver_query"] == "Os01g0177400"
    assert calls["arun"]["thread_id"] == _THREAD_ID


@pytest.mark.asyncio
async def test_new_identifier_replaces_active_gene_only_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed resolution keeps the active gene; success records the new one."""
    projection = _projection("AT1G01010", active=True, artifact_id=None)
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)

    async def fail_resolve(*_args: Any, **_kwargs: Any) -> Any:
        raise BriefGeneResolveError("ambiguous")

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", fail_resolve
    )
    clarification = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )
    assert clarification["choices"][0]["message"]["content"]
    assert adapter.active_gene_id == "Os01g0177400"

    async def resolve(_query: str, **_kwargs: Any) -> BriefGeneResolveResult:
        return _resolved("AT1G01010")

    async def arun(**_kwargs: Any) -> dict[str, Any]:
        """Return the successful replacement report fixture."""
        return _full_result(
            gene_id="AT1G01010",
            species_code="ath",
            artifact_id="new-report",
        )

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", resolve
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *args, **kwargs: SimpleNamespace(arun=arun),
    )
    await brief_gene_agent.brief_gene_function(
        "AT1G01010",
        conversation_adapter=adapter,
        conversation_projection=projection,
    )
    assert adapter.active_gene_id == "AT1G01010"


def test_delta_keeps_bounded_report_metadata_and_stable_thread() -> None:
    """Successful metadata is projected without copying the report body."""
    projection = _projection("Os01g0177400")
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)
    result = _full_result()
    adapter.capture_result(result, resolved=_resolved())

    delta = adapter.delta(result)
    ids = {entity.entity_id for entity in delta.entity_upserts}
    labels = {entity.label for entity in delta.entity_upserts}
    assert "brief_gene.gene.os01g0177400" in ids
    assert "brief_gene.species.osa" in ids
    assert "brief_gene.evidence.paper-1" in ids
    assert "brief_gene.report_revision.4" in ids
    assert "brief-report-1" in labels
    assert delta.summary_update is not None
    assert len(delta.summary_update) <= 4096
    assert delta.agent_memory_update is not None
    assert delta.agent_memory_update.thread_id == _THREAD_ID
    assert [item.artifact_id for item in delta.artifact_upserts] == [
        "brief-report-1"
    ]


@pytest.mark.parametrize(
    ("query", "result_kwargs", "expected_removals"),
    [
        (
            "refresh the report",
            {"revision": 5},
            {"brief_gene.report_revision.3"},
        ),
        (
            "AT1G01010",
            {
                "gene_id": "AT1G01010",
                "species_code": "ath",
                "artifact_id": "new-report",
                "revision": 1,
            },
            {
                "brief_gene.gene.os01g0177400",
                "brief_gene.species.osa",
                "brief_gene.report_revision.3",
            },
        ),
    ],
)
def test_delta_validates_successful_replacement_and_refresh(
    query: str,
    result_kwargs: dict[str, Any],
    expected_removals: set[str],
) -> None:
    """Successful refreshes and replacements produce valid removals."""
    projection = _projection(query, active=True, artifact_id=None)
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)
    result = _full_result(**result_kwargs)

    assert adapter.capture_result(result, resolved=_resolved()) is True

    delta = adapter.delta(result)
    validated = ContextDelta.model_validate(delta.model_dump())
    assert validated == delta
    assert expected_removals <= set(delta.entity_removals)
    assert all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entity.entity_id)
        for entity in delta.entity_upserts
    )
    assert all(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entity_id)
        for entity_id in delta.entity_removals
    )


def test_capture_result_stages_file_id_evidence_reference() -> None:
    """Production citation rows use file_id without retaining document text."""
    projection = _projection("Os01g0177400")
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)
    result = _full_result()
    result["choices"][0]["message"]["doc_list"] = [
        {
            "file_id": "paper-file-1",
            "source_id": "legacy-source-1",
            "title": "Paper",
            "content": "private report body",
        }
    ]

    assert adapter.capture_result(result, resolved=_resolved()) is True

    delta = adapter.delta(result)
    evidence = [
        entity.label
        for entity in delta.entity_upserts
        if entity.entity_id.startswith("brief_gene.evidence.")
    ]
    assert evidence == ["paper-file-1"]
    assert "private report body" not in str(delta.model_dump())


def test_brief_gene_invocation_keeps_private_state_and_stable_thread() -> None:
    """Context dispatch passes the operation adapter outside public
    arguments."""
    projection = _projection("Where is it expressed?", active=True)
    dispatch = brief_gene_agent_invocation(projection)
    assert dispatch.arguments == {
        "user_query": projection.current_query,
        "locale": projection.locale,
    }
    assert dispatch.agent_thread_id == _THREAD_ID
    assert dispatch.private_agent_state["brief_gene_adapter"].operation is (
        BriefGeneConversationOperation.FOLLOW_UP
    )
    assert dispatch.private_agent_state["brief_gene_projection"] is projection


@pytest.mark.asyncio
async def test_brief_gene_handler_forwards_private_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP handler passes the private thread and adapter to the wrapper."""
    captured: dict[str, Any] = {}

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(handlers, "brief_gene_function", wrapper)
    adapter = BriefGeneConversationAdapter()
    projection = _projection("Where is it expressed?", active=True)
    adapter.prepare(projection)
    result = await invoke_tool_raw(
        "BriefGeneAgent",
        {"user_query": projection.current_query},
        agent_thread_id=_THREAD_ID,
        private_agent_state={
            "brief_gene_adapter": adapter,
            "brief_gene_projection": projection,
        },
    )

    assert result == {"ok": True}
    assert captured["thread_id"] == _THREAD_ID
    assert captured["conversation_adapter"] is adapter
    assert captured["conversation_projection"] is projection


@pytest.mark.asyncio
async def test_brief_gene_context_without_projection_clarifies() -> None:
    """A missing projection cannot silently start a report workflow."""
    adapter = BriefGeneConversationAdapter()

    result = await brief_gene_agent.brief_gene_function(
        "Os01g0177400", conversation_adapter=adapter
    )

    assert result["choices"][0]["message"]["content"]
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_brief_gene_clarification_operation_is_not_stageable() -> None:
    """Ambiguous operations return clarification and mark the adapter failed."""
    projection = _projection("analyze a new gene", active=True)
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)

    result = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert result["choices"][0]["message"]["content"]
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_brief_gene_follow_up_clarification_error_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed follow-up is converted into a bounded clarification."""
    projection = _projection("Where is it expressed?", active=True)
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)

    async def fail_chat(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise BriefGeneClarificationError("follow-up unavailable")

    monkeypatch.setattr(brief_gene_agent, "invoke_brief_gene_chat", fail_chat)
    result = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert (
        "follow-up unavailable" in result["choices"][0]["message"]["content"]
    )
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_brief_gene_report_without_resolver_query_clarifies() -> None:
    """Report execution refuses to run when no identifier was prepared."""
    runtime = brief_gene_agent._build_brief_gene_runtime(None, {})
    adapter = BriefGeneConversationAdapter()

    result = await brief_gene_agent._run_brief_gene_conversation_report(
        adapter, runtime, None
    )

    assert result["choices"][0]["message"]["content"]
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_brief_gene_report_failure_marks_adapter_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent exception is propagated only after failure is recorded."""
    projection = _projection("Os01g0177400")
    adapter = BriefGeneConversationAdapter()
    adapter.prepare(projection)

    async def resolve(*_args: Any, **_kwargs: Any) -> BriefGeneResolveResult:
        return _resolved()

    async def fail_arun(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("report failed")

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", resolve
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *args, **kwargs: SimpleNamespace(arun=fail_arun),
    )

    with pytest.raises(RuntimeError, match="report failed"):
        await brief_gene_agent.brief_gene_function(
            projection.current_query,
            conversation_adapter=adapter,
            conversation_projection=projection,
        )
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_brief_gene_unusable_report_returns_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty report result is not captured as successful context."""
    projection = _projection("Os01g0177400")
    adapter = BriefGeneConversationAdapter()

    async def resolve(*_args: Any, **_kwargs: Any) -> BriefGeneResolveResult:
        return _resolved()

    async def empty_arun(**_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(
        brief_gene_agent, "resolve_brief_gene_user_query", resolve
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *args, **kwargs: SimpleNamespace(arun=empty_arun),
    )

    result = await brief_gene_agent.brief_gene_function(
        projection.current_query,
        conversation_adapter=adapter,
        conversation_projection=projection,
    )

    assert "usable report" in result["choices"][0]["message"]["content"]
    assert adapter.settlement_ready is False


def test_brief_gene_stream_seed_uses_shared_initial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stdio progress seed uses the cached app and shared state helper."""
    captured: dict[str, Any] = {}

    def fake_get_cached_agent(*args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(app="brief-app")

    monkeypatch.setattr(
        brief_gene_agent, "get_cached_agent", fake_get_cached_agent
    )
    args = SimpleNamespace(user_query="Os01g0177400", locale="en-US")

    app, state = brief_gene_agent.brief_gene_stream_seed(args)

    assert app == "brief-app"
    assert state["user_query"] == "Os01g0177400"
    assert captured["args"][0] == "BriefGeneAgent"
