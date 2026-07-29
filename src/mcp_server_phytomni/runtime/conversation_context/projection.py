# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Deterministic, bounded projections of Bot-owned conversation context."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, cast
from uuid import UUID

from ...config.defaults import ApiConfig
from ..locale import SupportedLocale
from .models import (
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    BusinessContext,
    ContextDelta,
    ContextEntity,
    ContextProjection,
    PerAgentMemory,
    RoleTaggedTurn,
)


class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...


class ConservativeTokenEstimator:
    """Estimate UTF-8 tokens without adding a tokenizer dependency."""

    def estimate(self, text: str) -> int:
        return max(1, (len(text.encode("utf-8")) + 2) // 3)


def _projection_budget_payload(
    *,
    current_query: str,
    intent_kind: str,
    task_summary: str,
    relevant_recent_turns: Sequence[RoleTaggedTurn | Mapping[str, str]],
    active_entities: Sequence[ContextEntity | Mapping[str, object]],
    open_questions: Sequence[str],
    artifact_refs: Sequence[ArtifactRefV1 | Mapping[str, object]],
    conversation_key: UUID,
    selected_agent_id: str,
    locale: SupportedLocale,
    token_budget: int,
    context_truncated: bool,
) -> dict[str, object]:
    """Serialize the exact bounded payload used for projection budgeting."""
    serialized_turns = [
        (
            turn.model_dump(mode="json")
            if isinstance(turn, RoleTaggedTurn)
            else dict(turn)
        )
        for turn in relevant_recent_turns
    ]
    user_turns: list[str] = []
    assistant_summaries: list[str] = []
    for turn in serialized_turns:
        role = turn["role"]
        content = turn["content"]
        if role == "user":
            user_turns.append(content)
        else:
            assistant_summaries.append(content)
    return {
        "current_query": current_query,
        "intent_kind": intent_kind,
        "task_summary": task_summary,
        "relevant_recent_turns": serialized_turns,
        "relevant_user_turns": user_turns,
        "relevant_assistant_summaries": assistant_summaries,
        "active_entities": [
            (
                item.model_dump(mode="json")
                if isinstance(item, ContextEntity)
                else dict(item)
            )
            for item in active_entities
        ],
        "open_questions": list(open_questions),
        "artifact_refs": [
            (
                item.model_dump(mode="json")
                if isinstance(item, ArtifactRefV1)
                else dict(item)
            )
            for item in artifact_refs
        ],
        "agent_thread_id": agent_thread_id(
            conversation_key, selected_agent_id
        ),
        "locale": locale,
        "token_budget": token_budget,
        "context_truncated": context_truncated,
    }


def agent_thread_id(conversation_key: UUID, agent_id: str) -> str:
    """Derive an opaque, stable thread identifier for one agent namespace."""
    digest = hashlib.sha256(
        f"conversation-context-v1:{conversation_key}:{agent_id}".encode(
            "ascii"
        )
    ).hexdigest()
    return f"ctx-{digest}"


def _budget(config: ApiConfig, agent_id: str) -> int:
    budgets = {
        "ChatAgent": config.CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET,
        "KnowledgeAgent": config.CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET,
        "DataAgent": config.CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET,
        "ReviewAgent": config.CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET,
        "BriefGeneAgent": config.CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET,
    }
    try:
        return budgets[agent_id]
    except KeyError as exc:
        raise ValueError(f"unsupported context agent: {agent_id}") from exc


def build_context_projection(
    *,
    conversation_key: UUID,
    current_query: str,
    locale: SupportedLocale,
    selected_agent_id: str,
    context: BusinessContext,
    authorized_artifacts: Sequence[ArtifactRefV1],
    api_config: ApiConfig,
    estimator: TokenEstimator | None = None,
    exclude_current_user_turn: bool = False,
) -> ContextProjection:
    """Admit context sections in the documented priority order."""
    estimator = estimator or ConservativeTokenEstimator()
    budget = _budget(api_config, selected_agent_id)
    result: dict[str, object] = {}
    truncated = False

    def payload() -> dict[str, object]:
        """Build the complete serialized wrapper used for every estimate."""
        return _projection_budget_payload(
            current_query=str(result.get("current_query", "")),
            intent_kind=str(result.get("intent_kind", "follow_up")),
            task_summary=str(result.get("task_summary", "")),
            relevant_recent_turns=cast(
                Sequence[RoleTaggedTurn | Mapping[str, str]],
                result.get("relevant_recent_turns", []),
            ),
            active_entities=cast(
                Sequence[ContextEntity | Mapping[str, object]],
                result.get("active_entities", []),
            ),
            open_questions=cast(
                Sequence[str], result.get("open_questions", [])
            ),
            artifact_refs=cast(
                Sequence[ArtifactRefV1 | Mapping[str, object]],
                result.get("artifact_refs", []),
            ),
            conversation_key=conversation_key,
            selected_agent_id=selected_agent_id,
            locale=locale,
            token_budget=budget,
            # False is one byte larger than true in JSON, so this reserves the
            # maximum size for the final boolean metadata field.
            context_truncated=False,
        )

    def fits() -> bool:
        return (
            estimator.estimate(
                json.dumps(payload(), ensure_ascii=False, sort_keys=True)
            )
            <= budget
        )

    # Fit the highest-priority query before admitting recovered context and
    # account for the final wrapper on every attempt.
    query_limit = min(len(current_query), MAX_CONTEXT_TEXT_CHARS * 8)
    while query_limit > 1:
        result["current_query"] = current_query[:query_limit]
        if fits():
            break
        query_limit = max(1, query_limit // 2)
    result["current_query"] = current_query[:query_limit]
    if query_limit < len(current_query):
        truncated = True

    def admit(name: str, value: object) -> None:
        """Admit a whole scalar or the longest ordered prefix of a list."""
        nonlocal truncated
        if not value:
            return
        if not isinstance(value, list):
            result[name] = value
            if fits():
                return
            result.pop(name)
            truncated = True
            return

        admitted: list[object] = []
        for item in value:
            result[name] = [*admitted, item]
            if fits():
                admitted.append(item)
            else:
                truncated = True
                break
        if len(admitted) < len(value):
            truncated = True
        if admitted:
            result[name] = admitted
        else:
            result.pop(name, None)

    admit(
        "active_entities",
        [item.model_dump(mode="json") for item in context.active_entities],
    )
    admit("open_questions", context.open_questions)
    recent_turns = list(context.recent_turns)
    if (
        exclude_current_user_turn
        and recent_turns
        and recent_turns[-1].role == "user"
    ):
        # Rebuilt contexts include the envelope's current user turn as their
        # trailing slot; dispatch carries it separately as current_query.
        recent_turns = recent_turns[:-1]
    admitted_turns: list[dict[str, str]] = []
    for turn in [item.model_dump(mode="json") for item in recent_turns]:
        result["relevant_recent_turns"] = [*admitted_turns, turn]
        if fits():
            admitted_turns.append(turn)
        else:
            truncated = True
            break
    if len(admitted_turns) < len(recent_turns):
        truncated = True
    if admitted_turns:
        result["relevant_recent_turns"] = admitted_turns
    else:
        result.pop("relevant_recent_turns", None)
    envelope_ids = {item.artifact_id for item in authorized_artifacts}
    artifacts = [
        item.model_dump(mode="json")
        for item in context.artifact_index
        if item.artifact_id in envelope_ids
    ]
    admit("artifact_refs", artifacts)
    admit("task_summary", context.task_summary)

    projection = ContextProjection(
        current_query=cast(str, result["current_query"]),
        task_summary=cast(str, result.get("task_summary", "")),
        relevant_recent_turns=cast(
            list[RoleTaggedTurn], result.get("relevant_recent_turns", [])
        ),
        active_entities=cast(
            list[ContextEntity], result.get("active_entities", [])
        ),
        open_questions=cast(list[str], result.get("open_questions", [])),
        artifact_refs=cast(
            list[ArtifactRefV1], result.get("artifact_refs", [])
        ),
        agent_thread_id=agent_thread_id(conversation_key, selected_agent_id),
        locale=locale,
        token_budget=budget,
        context_truncated=truncated,
    )
    if (
        estimator.estimate(
            json.dumps(
                _projection_budget_payload(
                    current_query=projection.current_query,
                    intent_kind=projection.intent_kind,
                    task_summary=projection.task_summary,
                    relevant_recent_turns=projection.relevant_recent_turns,
                    active_entities=projection.active_entities,
                    open_questions=projection.open_questions,
                    artifact_refs=projection.artifact_refs,
                    conversation_key=conversation_key,
                    selected_agent_id=selected_agent_id,
                    locale=projection.locale,
                    token_budget=projection.token_budget,
                    context_truncated=projection.context_truncated,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        > budget
    ):
        raise ValueError("context projection metadata exceeds token budget")
    return projection


def rebuild_business_context(
    *,
    conversation_key: UUID,
    ledger_entries: Sequence[Mapping[str, object]],
    artifact_refs: Sequence[ArtifactRefV1],
    ledger_cursor: int,
    ledger_version: str,
    observed_mode: Literal["instant", "expert"],
) -> BusinessContext:
    """Rebuild semantic context from bounded, ordered ledger input."""
    recent_turns: list[RoleTaggedTurn] = []

    def bound_text(value: str) -> str:
        """Keep both ends of oversized text while preserving a fixed bound."""
        if len(value) <= MAX_CONTEXT_TEXT_CHARS:
            return value
        prefix = MAX_CONTEXT_TEXT_CHARS // 2
        return value[:prefix] + value[-(MAX_CONTEXT_TEXT_CHARS - prefix) :]

    for raw in ledger_entries:
        role = raw.get("role")
        content = raw.get("content")
        summary = raw.get("summary")
        if role == "user" and isinstance(content, str):
            recent_turns.append(
                RoleTaggedTurn(role="user", content=bound_text(content))
            )
        elif role == "assistant" and isinstance(summary, str):
            recent_turns.append(
                RoleTaggedTurn(
                    role="assistant",
                    content=bound_text(summary),
                )
            )
    return BusinessContext(
        schema_version=1,
        version=0,
        last_applied_ledger_cursor=ledger_cursor,
        last_applied_ledger_version=ledger_version,
        observed_mode=observed_mode,
        recent_turns=recent_turns[-50:],
        artifact_index=list(artifact_refs),
        per_agent_memory={
            agent: PerAgentMemory(
                agent_id=agent,
                thread_id=agent_thread_id(conversation_key, agent),
            )
            for agent in (
                "ChatAgent",
                "KnowledgeAgent",
                "DataAgent",
                "ReviewAgent",
                "BriefGeneAgent",
            )
        },
    )


def validate_context_delta(
    delta: ContextDelta,
    *,
    conversation_key: UUID,
    selected_agent_id: str,
    authorized_artifact_ids: set[str],
) -> None:
    """Reject changes outside the selected namespace or envelope allowlist."""
    unknown = {
        artifact.artifact_id
        for artifact in delta.artifact_upserts
        if artifact.artifact_id not in authorized_artifact_ids
    }
    if unknown:
        raise ValueError("artifact reference is not authorized")
    memory = delta.agent_memory_update
    if memory is not None:
        if memory.agent_id != selected_agent_id:
            raise ValueError("agent memory must belong to the selected agent")
        if (
            memory.checkpoint_ref is not None
            and memory.checkpoint_ref not in authorized_artifact_ids
        ):
            raise ValueError("checkpoint reference is not authorized")
        if memory.thread_id != agent_thread_id(
            conversation_key, selected_agent_id
        ):
            raise ValueError(
                "agent memory thread does not belong to the conversation"
            )


__all__ = [
    "ConservativeTokenEstimator",
    "TokenEstimator",
    "agent_thread_id",
    "build_context_projection",
    "rebuild_business_context",
    "validate_context_delta",
]
