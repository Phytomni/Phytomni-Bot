# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Deterministic, bounded projections of Bot-owned conversation context."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from ...config.defaults import ApiConfig
from .models import (
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    BusinessContext,
    ContextDelta,
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
    locale: str,
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
        return {
            "current_query": result.get("current_query", ""),
            "intent_kind": result.get("intent_kind", "follow_up"),
            "task_summary": result.get("task_summary", ""),
            "relevant_user_turns": result.get("relevant_user_turns", []),
            "relevant_assistant_summaries": result.get(
                "relevant_assistant_summaries", []
            ),
            "active_entities": result.get("active_entities", []),
            "open_questions": result.get("open_questions", []),
            "artifact_refs": result.get("artifact_refs", []),
            "agent_thread_id": agent_thread_id(
                conversation_key, selected_agent_id
            ),
            "locale": locale,
            "token_budget": budget,
            # False is one byte larger than true in JSON, so this reserves the
            # maximum size for the final boolean metadata field.
            "context_truncated": False,
        }

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

    def sync_recent_turn_compatibility() -> None:
        turns = result.get("relevant_recent_turns", [])
        user_turns: list[str] = []
        assistant_summaries: list[str] = []
        for turn in turns:
            role = turn["role"]
            content = turn["content"]
            if role == "user":
                user_turns.append(content)
            else:
                assistant_summaries.append(content)
        result["relevant_user_turns"] = user_turns
        result["relevant_assistant_summaries"] = assistant_summaries

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
        # trailing slot; dispatch carries that turn separately as current_query.
        recent_turns = recent_turns[:-1]
    admitted_turns: list[dict[str, str]] = []
    for turn in [item.model_dump(mode="json") for item in recent_turns]:
        result["relevant_recent_turns"] = [*admitted_turns, turn]
        sync_recent_turn_compatibility()
        if fits():
            admitted_turns.append(turn)
        else:
            truncated = True
            break
    if len(admitted_turns) < len(recent_turns):
        truncated = True
    if admitted_turns:
        result["relevant_recent_turns"] = admitted_turns
        sync_recent_turn_compatibility()
    else:
        result.pop("relevant_recent_turns", None)
        result.pop("relevant_user_turns", None)
        result.pop("relevant_assistant_summaries", None)
    envelope_ids = {item.artifact_id for item in authorized_artifacts}
    artifacts = [
        item.model_dump(mode="json")
        for item in context.artifact_index
        if item.artifact_id in envelope_ids
    ]
    admit("artifact_refs", artifacts)
    admit("task_summary", context.task_summary)

    projection = ContextProjection(
        current_query=result["current_query"],
        task_summary=result.get("task_summary", ""),
        relevant_recent_turns=result.get("relevant_recent_turns", []),
        active_entities=result.get("active_entities", []),
        open_questions=result.get("open_questions", []),
        artifact_refs=result.get("artifact_refs", []),
        agent_thread_id=agent_thread_id(conversation_key, selected_agent_id),
        locale=locale,
        token_budget=budget,
        context_truncated=truncated,
    )
    if (
        estimator.estimate(
            json.dumps(
                projection.model_dump(mode="json"),
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
    observed_mode: str,
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
        if role == "user" and isinstance(raw.get("content"), str):
            recent_turns.append(
                RoleTaggedTurn(role="user", content=bound_text(raw["content"]))
            )
        elif role == "assistant" and isinstance(raw.get("summary"), str):
            recent_turns.append(
                RoleTaggedTurn(
                    role="assistant",
                    content=bound_text(raw["summary"]),
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
