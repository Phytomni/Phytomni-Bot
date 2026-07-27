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
    ArtifactRefV1,
    BusinessContext,
    ContextDelta,
    ContextProjection,
    PerAgentMemory,
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
        f"conversation-context-v1:{conversation_key}:{agent_id}".encode("ascii")
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
) -> ContextProjection:
    """Admit context sections in the documented priority order."""
    estimator = estimator or ConservativeTokenEstimator()
    budget = _budget(api_config, selected_agent_id)
    result: dict[str, object] = {"current_query": current_query}
    truncated = False

    def admit(name: str, value: object) -> None:
        """Admit a whole scalar or the longest ordered prefix of a list."""
        nonlocal truncated
        if not value:
            return
        if not isinstance(value, list):
            candidate = {**result, name: value}
            if estimator.estimate(
                json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            ) <= budget:
                result[name] = value
            else:
                truncated = True
            return

        admitted: list[object] = []
        for item in value:
            candidate = {**result, name: [*admitted, item]}
            if estimator.estimate(
                json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            ) > budget:
                truncated = True
                break
            admitted.append(item)
        if len(admitted) < len(value):
            truncated = True
        if admitted:
            result[name] = admitted

    admit(
        "active_entities",
        [item.model_dump(mode="json") for item in context.active_entities],
    )
    admit("open_questions", context.open_questions)
    admit("relevant_user_turns", context.recent_user_turns)
    admit("relevant_assistant_summaries", context.assistant_summaries)
    envelope_ids = {item.artifact_id for item in authorized_artifacts}
    artifacts = [
        item.model_dump(mode="json")
        for item in context.artifact_index
        if item.artifact_id in envelope_ids
    ]
    admit("artifact_refs", artifacts)
    admit("task_summary", context.task_summary)

    return ContextProjection(
        current_query=current_query,
        task_summary=result.get("task_summary", ""),
        relevant_user_turns=result.get("relevant_user_turns", []),
        relevant_assistant_summaries=result.get("relevant_assistant_summaries", []),
        active_entities=result.get("active_entities", []),
        open_questions=result.get("open_questions", []),
        artifact_refs=result.get("artifact_refs", []),
        agent_thread_id=agent_thread_id(conversation_key, selected_agent_id),
        locale=locale,
        token_budget=budget,
        context_truncated=truncated,
    )


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
    users: list[str] = []
    summaries: list[str] = []
    for raw in ledger_entries:
        role = raw.get("role")
        if role == "user" and isinstance(raw.get("content"), str):
            users.append(raw["content"])
        elif role == "assistant" and isinstance(raw.get("summary"), str):
            summaries.append(raw["summary"])
    return BusinessContext(
        schema_version=1,
        version=0,
        last_applied_ledger_cursor=ledger_cursor,
        last_applied_ledger_version=ledger_version,
        observed_mode=observed_mode,
        recent_user_turns=users,
        assistant_summaries=summaries,
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
    if memory is not None and memory.agent_id != selected_agent_id:
        raise ValueError("agent memory must belong to the selected agent")


__all__ = [
    "ConservativeTokenEstimator",
    "TokenEstimator",
    "agent_thread_id",
    "build_context_projection",
    "rebuild_business_context",
    "validate_context_delta",
]
