# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Unit tests for bounded, agent-specific conversation context projections."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.runtime.conversation_context.models import (
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    BusinessContext,
    ContextDelta,
    ContextEntity,
    ContextStageMetadata,
    PerAgentMemory,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
    build_context_projection,
    rebuild_business_context,
    validate_context_delta,
)

pytestmark = pytest.mark.unit

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6")


def _config() -> ApiConfig:
    """Use the minimum configured DataAgent budget for trimming coverage."""
    return ApiConfig(CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET=512)


def _envelope_artifact() -> ArtifactRefV1:
    """Return an owner-authorized, path-free artifact reference."""
    return ArtifactRefV1(artifact_id="artifact-1", display_name="r" * 400)


def _context() -> BusinessContext:
    """Return context whose sections exceed the selected agent budget in order."""
    return BusinessContext(
        schema_version=1,
        version=7,
        last_applied_ledger_cursor=9,
        last_applied_ledger_version="a" * 64,
        observed_mode="expert",
        task_summary="s" * 200,
        active_entities=[
            ContextEntity(
                entity_id="gene-1", entity_type="gene", label="g" * 50
            )
        ],
        open_questions=["q" * 50],
        recent_user_turns=["u" * 300],
        assistant_summaries=["a" * 100],
        artifact_index=[_envelope_artifact()],
        per_agent_memory={
            "DataAgent": PerAgentMemory(
                agent_id="DataAgent",
                thread_id=agent_thread_id(_CONVERSATION_KEY, "DataAgent"),
                summary="working state",
            )
        },
    )


def test_projection_trims_in_documented_priority_order() -> None:
    """Query truncates before context admission under a tight budget."""

    class CharacterEstimator:
        def estimate(self, text: str) -> int:
            return len(text)

    projection = build_context_projection(
        conversation_key=_CONVERSATION_KEY,
        current_query="c" * 200,
        locale="en-US",
        selected_agent_id="DataAgent",
        context=_context(),
        authorized_artifacts=[_envelope_artifact()],
        api_config=_config(),
        estimator=CharacterEstimator(),
    )

    assert projection.current_query != "c" * 200
    assert projection.current_query == "c" * len(projection.current_query)
    assert len(projection.current_query) < 200
    assert projection.active_entities == []
    assert projection.open_questions == ["q" * 50]
    assert projection.relevant_user_turns == []
    assert projection.relevant_assistant_summaries == []
    assert projection.artifact_refs == []
    assert projection.task_summary == ""
    assert projection.context_truncated is True
    assert (
        CharacterEstimator().estimate(projection.model_dump_json())
        <= projection.token_budget
    )


def test_projection_is_deterministic_and_uses_configured_agent_budget() -> (
    None
):
    """The same ordered source data rebuilds to byte-equivalent projection data."""
    context = _context()
    kwargs = {
        "conversation_key": _CONVERSATION_KEY,
        "current_query": "follow up",
        "locale": "en-US",
        "selected_agent_id": "DataAgent",
        "context": context,
        "authorized_artifacts": [_envelope_artifact()],
        "api_config": _config(),
    }

    first = build_context_projection(**kwargs)
    second = build_context_projection(**kwargs)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.token_budget == 512
    assert first.agent_thread_id == agent_thread_id(
        _CONVERSATION_KEY, "DataAgent"
    )


def test_rebuild_is_deterministic_and_keeps_only_assistant_summaries() -> None:
    """Recovery never copies a full assistant report into shared context."""
    entries = [
        {"turn_id": "1", "role": "user", "content": "Find rice genes."},
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "full report body that must not be retained",
            "summary": "Found two candidate genes.",
        },
    ]

    first = rebuild_business_context(
        conversation_key=_CONVERSATION_KEY,
        ledger_entries=entries,
        artifact_refs=[_envelope_artifact()],
        ledger_cursor=2,
        ledger_version="b" * 64,
        observed_mode="expert",
    )
    second = rebuild_business_context(
        conversation_key=_CONVERSATION_KEY,
        ledger_entries=entries,
        artifact_refs=[_envelope_artifact()],
        ledger_cursor=2,
        ledger_version="b" * 64,
        observed_mode="expert",
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.recent_user_turns == ["Find rice genes."]
    assert first.assistant_summaries == ["Found two candidate genes."]
    assert "full report" not in first.model_dump_json()


def test_agent_thread_id_is_opaque_and_stable() -> None:
    """Thread IDs use a stable digest rather than public conversation values."""
    thread_id = agent_thread_id(_CONVERSATION_KEY, "DataAgent")

    assert thread_id.startswith("ctx-")
    assert len(thread_id) == 68
    assert str(_CONVERSATION_KEY) not in thread_id
    assert "DataAgent" not in thread_id
    assert thread_id == agent_thread_id(_CONVERSATION_KEY, "DataAgent")


@pytest.mark.parametrize(
    "artifact",
    [
        {
            "artifact_id": "obs://bucket/report.csv",
            "display_name": "report.csv",
        },
        {"artifact_id": "artifact-1", "display_name": "/srv/report.csv"},
    ],
)
def test_artifact_models_reject_raw_storage_paths(
    artifact: dict[str, str],
) -> None:
    """Projection metadata cannot turn storage locations into context data."""
    with pytest.raises(ValidationError):
        ArtifactRefV1.model_validate(artifact)


def test_delta_rejects_artifact_outside_current_envelope_allowlist() -> None:
    """An agent can retain only references authorized for this envelope."""
    delta = ContextDelta(
        artifact_upserts=[
            ArtifactRefV1(artifact_id="artifact-2", display_name="other.csv")
        ]
    )

    with pytest.raises(ValueError, match="authorized"):
        validate_context_delta(
            delta,
            conversation_key=_CONVERSATION_KEY,
            selected_agent_id="DataAgent",
            authorized_artifact_ids={"artifact-1"},
        )


def test_delta_rejects_other_agent_memory_namespace() -> None:
    """The selected agent cannot replace another agent's checkpoint state."""
    delta = ContextDelta(
        agent_memory_update=PerAgentMemory(
            agent_id="KnowledgeAgent",
            thread_id=agent_thread_id(_CONVERSATION_KEY, "KnowledgeAgent"),
            summary="retrieval state",
        )
    )

    with pytest.raises(ValueError, match="selected agent"):
        validate_context_delta(
            delta,
            conversation_key=_CONVERSATION_KEY,
            selected_agent_id="DataAgent",
            authorized_artifact_ids=set(),
        )


@pytest.mark.parametrize(
    "checkpoint_ref",
    ["/srv/checkpoint", "s3://bucket/checkpoint", "file:checkpoint"],
)
def test_memory_rejects_path_or_uri_checkpoint_ref(
    checkpoint_ref: str,
) -> None:
    """Checkpoint references cannot smuggle storage locations into context."""
    with pytest.raises(ValidationError, match="checkpoint_ref"):
        PerAgentMemory(
            agent_id="DataAgent",
            thread_id=agent_thread_id(_CONVERSATION_KEY, "DataAgent"),
            checkpoint_ref=checkpoint_ref,
        )


def test_delta_rejects_checkpoint_outside_current_envelope_allowlist() -> None:
    """Checkpoint references use the same artifact authorization boundary."""
    delta = ContextDelta(
        agent_memory_update=PerAgentMemory(
            agent_id="DataAgent",
            thread_id=agent_thread_id(_CONVERSATION_KEY, "DataAgent"),
            checkpoint_ref="artifact-2",
        )
    )

    with pytest.raises(ValueError, match="checkpoint"):
        validate_context_delta(
            delta,
            conversation_key=_CONVERSATION_KEY,
            selected_agent_id="DataAgent",
            authorized_artifact_ids={"artifact-1"},
        )


def test_delta_rejects_memory_thread_from_another_conversation() -> None:
    """A selected agent thread is scoped to both conversation and agent."""
    delta = ContextDelta(
        agent_memory_update=PerAgentMemory(
            agent_id="DataAgent",
            thread_id=agent_thread_id(
                UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"), "DataAgent"
            ),
        )
    )

    with pytest.raises(ValueError, match="conversation"):
        validate_context_delta(
            delta,
            conversation_key=_CONVERSATION_KEY,
            selected_agent_id="DataAgent",
            authorized_artifact_ids=set(),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"summary_update": "x" * 4097},
        {
            "agent_memory_update": {
                "agent_id": "DataAgent",
                "thread_id": agent_thread_id(_CONVERSATION_KEY, "DataAgent"),
                "summary": "x" * 4097,
            }
        },
    ],
)
def test_delta_rejects_full_report_or_table_sized_fields(
    payload: dict[str, object],
) -> None:
    """Large generated reports and tables do not become semantic context."""
    with pytest.raises(ValidationError):
        ContextDelta.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"entity_removals": ["x" * 129]},
        {"open_question_updates": ["x" * (MAX_CONTEXT_TEXT_CHARS + 1)]},
    ],
)
def test_delta_rejects_oversized_list_items(
    payload: dict[str, object],
) -> None:
    """Delta items remain semantic snippets rather than report payloads."""
    with pytest.raises(ValidationError):
        ContextDelta.model_validate(payload)


@pytest.mark.parametrize(
    "field", ["open_questions", "recent_user_turns", "assistant_summaries"]
)
def test_business_context_rejects_oversized_list_items(field: str) -> None:
    """Recovered context lists reject individual report-sized strings."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "version": 0,
        "last_applied_ledger_cursor": 0,
        "last_applied_ledger_version": "a" * 64,
        "observed_mode": "expert",
        field: ["x" * (MAX_CONTEXT_TEXT_CHARS + 1)],
    }

    with pytest.raises(ValidationError):
        BusinessContext.model_validate(payload)


def test_rebuild_bounds_large_ledgers_and_preserves_recent_summaries() -> None:
    """Recovery accepts long ledgers and retains bounded recent data."""
    entries = [
        {"turn_id": str(index + 1), "role": "user", "content": f"user-{index}"}
        for index in range(100)
    ]
    entries.extend(
        {
            "turn_id": str(index + 101),
            "role": "assistant",
            "content": "full report must not be retained",
            "summary": f"summary-{index}",
        }
        for index in range(100)
    )

    context = rebuild_business_context(
        conversation_key=_CONVERSATION_KEY,
        ledger_entries=entries,
        artifact_refs=[],
        ledger_cursor=200,
        ledger_version="b" * 64,
        observed_mode="expert",
    )

    assert len(context.recent_user_turns) == 50
    assert context.recent_user_turns[0] == "user-50"
    assert context.recent_user_turns[-1] == "user-99"
    assert len(context.assistant_summaries) == 50
    assert context.assistant_summaries[0] == "summary-50"
    assert context.assistant_summaries[-1] == "summary-99"
    assert all(
        len(item) <= MAX_CONTEXT_TEXT_CHARS
        for item in context.recent_user_turns
    )
    assert "full report" not in context.model_dump_json()


def test_context_rejects_unknown_entity_type() -> None:
    """Entities use a constrained semantic vocabulary."""
    with pytest.raises(ValidationError, match="entity_type"):
        ContextEntity(
            entity_id="unknown-1", entity_type="unknown", label="bad"
        )


@pytest.mark.parametrize(
    "field",
    ["raw_reasoning", "permission_list", "allowed_agent_ids"],
)
def test_stage_metadata_rejects_reasoning_and_permission_lists(
    field: str,
) -> None:
    """Routing results expose only stable, non-sensitive reason codes."""
    payload: dict[str, object] = {
        "selected_agent_id": "DataAgent",
        "route_source": "router",
        "route_reason_code": "ROUTER_SELECTED",
        "base_business_context_version": 7,
        "proposed_business_context_version": 8,
        "last_applied_ledger_cursor": 9,
        "context_truncated": False,
        "context_rebuilt": False,
        field: "sensitive metadata",
    }

    with pytest.raises(
        ValidationError, match="Extra inputs are not permitted"
    ):
        ContextStageMetadata.model_validate(payload)
