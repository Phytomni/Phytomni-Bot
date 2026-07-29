# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Deterministic, bounded projections of Bot-owned conversation context."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, NamedTuple, Protocol, cast
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


class _ProjectionBudget(NamedTuple):
    """Inputs that define one exact serialized projection budget payload."""

    current_query: str
    intent_kind: str
    task_summary: str
    relevant_recent_turns: Sequence[RoleTaggedTurn | Mapping[str, str]]
    active_entities: Sequence[ContextEntity | Mapping[str, object]]
    open_questions: Sequence[str]
    artifact_refs: Sequence[ArtifactRefV1 | Mapping[str, object]]
    conversation_key: UUID
    selected_agent_id: str
    locale: SupportedLocale
    token_budget: int
    context_truncated: bool


class _ProjectionRequest(NamedTuple):
    """Validated arguments for the public projection builder."""

    conversation_key: UUID
    current_query: str
    locale: SupportedLocale
    selected_agent_id: str
    context: BusinessContext
    authorized_artifacts: Sequence[ArtifactRefV1]
    api_config: ApiConfig
    estimator: TokenEstimator | None
    exclude_current_user_turn: bool


class _RebuildRequest(NamedTuple):
    """Validated arguments for semantic context reconstruction."""

    conversation_key: UUID
    ledger_entries: Sequence[Mapping[str, object]]
    artifact_refs: Sequence[ArtifactRefV1]
    ledger_cursor: int
    ledger_version: str
    observed_mode: Literal["instant", "expert"]


def _projection_budget_payload(
    request: _ProjectionBudget | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """Serialize the exact bounded payload used for projection budgeting."""
    if request is None:
        request = _ProjectionBudget(
            current_query=cast(str, kwargs.pop("current_query")),
            intent_kind=cast(str, kwargs.pop("intent_kind")),
            task_summary=cast(str, kwargs.pop("task_summary")),
            relevant_recent_turns=cast(
                Sequence[RoleTaggedTurn | Mapping[str, str]],
                kwargs.pop("relevant_recent_turns"),
            ),
            active_entities=cast(
                Sequence[ContextEntity | Mapping[str, object]],
                kwargs.pop("active_entities"),
            ),
            open_questions=cast(
                Sequence[str], kwargs.pop("open_questions")
            ),
            artifact_refs=cast(
                Sequence[ArtifactRefV1 | Mapping[str, object]],
                kwargs.pop("artifact_refs"),
            ),
            conversation_key=cast(UUID, kwargs.pop("conversation_key")),
            selected_agent_id=cast(str, kwargs.pop("selected_agent_id")),
            locale=cast(SupportedLocale, kwargs.pop("locale")),
            token_budget=cast(int, kwargs.pop("token_budget")),
            context_truncated=cast(bool, kwargs.pop("context_truncated")),
        )
    if kwargs:
        unexpected = ", ".join(sorted(kwargs))
        raise TypeError(f"unexpected projection budget fields: {unexpected}")
    serialized_turns = [
        (
            turn.model_dump(mode="json")
            if isinstance(turn, RoleTaggedTurn)
            else dict(turn)
        )
        for turn in request.relevant_recent_turns
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
        "current_query": request.current_query,
        "intent_kind": request.intent_kind,
        "task_summary": request.task_summary,
        "relevant_recent_turns": serialized_turns,
        "relevant_user_turns": user_turns,
        "relevant_assistant_summaries": assistant_summaries,
        "active_entities": [
            (
                item.model_dump(mode="json")
                if isinstance(item, ContextEntity)
                else dict(item)
            )
            for item in request.active_entities
        ],
        "open_questions": list(request.open_questions),
        "artifact_refs": [
            (
                item.model_dump(mode="json")
                if isinstance(item, ArtifactRefV1)
                else dict(item)
            )
            for item in request.artifact_refs
        ],
        "agent_thread_id": agent_thread_id(
            request.conversation_key, request.selected_agent_id
        ),
        "locale": request.locale,
        "token_budget": request.token_budget,
        "context_truncated": request.context_truncated,
    }


def _ensure_projection_within_budget(
    projection: ContextProjection,
    request: _ProjectionRequest,
    estimator: TokenEstimator,
    budget: int,
) -> None:
    """Verify the final private projection payload against its budget."""
    payload = _ProjectionBudget(
        current_query=projection.current_query,
        intent_kind=projection.intent_kind,
        task_summary=projection.task_summary,
        relevant_recent_turns=projection.relevant_recent_turns,
        active_entities=projection.active_entities,
        open_questions=projection.open_questions,
        artifact_refs=projection.artifact_refs,
        conversation_key=request.conversation_key,
        selected_agent_id=request.selected_agent_id,
        locale=projection.locale,
        token_budget=projection.token_budget,
        context_truncated=projection.context_truncated,
    )
    serialized = json.dumps(
        _projection_budget_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
    )
    if estimator.estimate(serialized) > budget:
        raise ValueError("context projection metadata exceeds token budget")


def _admit_projection_value(
    result: dict[str, object],
    name: str,
    value: object,
    fits: Callable[[], bool],
) -> bool:
    """Admit a scalar or longest ordered list prefix and report truncation."""
    if not value:
        return False
    if not isinstance(value, list):
        result[name] = value
        if fits():
            return False
        result.pop(name)
        return True

    admitted: list[object] = []
    truncated = False
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
    return truncated


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


_PROJECTION_FIELDS = frozenset(
    {
        "conversation_key",
        "current_query",
        "locale",
        "selected_agent_id",
        "context",
        "authorized_artifacts",
        "api_config",
        "estimator",
        "exclude_current_user_turn",
    }
)


def _projection_request(values: Mapping[str, object]) -> _ProjectionRequest:
    """Convert keyword-compatible public arguments into one value object."""
    unexpected = set(values) - _PROJECTION_FIELDS
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise TypeError(f"unexpected projection fields: {names}")
    required = _PROJECTION_FIELDS - {"estimator", "exclude_current_user_turn"}
    missing = required - set(values)
    if missing:
        names = ", ".join(sorted(missing))
        raise TypeError(f"missing projection fields: {names}")
    return _ProjectionRequest(
        conversation_key=cast(UUID, values["conversation_key"]),
        current_query=cast(str, values["current_query"]),
        locale=cast(SupportedLocale, values["locale"]),
        selected_agent_id=cast(str, values["selected_agent_id"]),
        context=cast(BusinessContext, values["context"]),
        authorized_artifacts=cast(
            Sequence[ArtifactRefV1], values["authorized_artifacts"]
        ),
        api_config=cast(ApiConfig, values["api_config"]),
        estimator=cast(TokenEstimator | None, values.get("estimator")),
        exclude_current_user_turn=cast(
            bool, values.get("exclude_current_user_turn", False)
        ),
    )


def build_context_projection(**kwargs: object) -> ContextProjection:
    """Admit context sections in the documented priority order."""
    return _build_context_projection(_projection_request(kwargs))


def _build_context_projection(request: _ProjectionRequest) -> ContextProjection:
    """Admit context sections in the documented priority order."""
    estimator = request.estimator or ConservativeTokenEstimator()
    budget = _budget(request.api_config, request.selected_agent_id)
    result: dict[str, object] = {}
    truncated = False

    def payload() -> dict[str, object]:
        """Build the complete serialized wrapper used for every estimate."""
        return _projection_budget_payload(
            _ProjectionBudget(
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
                conversation_key=request.conversation_key,
                selected_agent_id=request.selected_agent_id,
                locale=request.locale,
                token_budget=budget,
                # False is one byte larger than true in JSON, so this reserves
                # the maximum size for the final boolean metadata field.
                context_truncated=False,
            )
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
    query_limit = min(len(request.current_query), MAX_CONTEXT_TEXT_CHARS * 8)
    while query_limit > 1:
        result["current_query"] = request.current_query[:query_limit]
        if fits():
            break
        query_limit = max(1, query_limit // 2)
    result["current_query"] = request.current_query[:query_limit]
    if query_limit < len(request.current_query):
        truncated = True

    truncated = _admit_projection_value(
        result,
        "active_entities",
        [item.model_dump(mode="json") for item in request.context.active_entities],
        fits,
    ) or truncated
    truncated = (
        _admit_projection_value(
            result, "open_questions", request.context.open_questions, fits
        )
        or truncated
    )
    recent_turns = list(request.context.recent_turns)
    if (
        request.exclude_current_user_turn
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
    envelope_ids = {item.artifact_id for item in request.authorized_artifacts}
    artifacts = [
        item.model_dump(mode="json")
        for item in request.context.artifact_index
        if item.artifact_id in envelope_ids
    ]
    truncated = (
        _admit_projection_value(result, "artifact_refs", artifacts, fits)
        or truncated
    )
    truncated = (
        _admit_projection_value(
            result, "task_summary", request.context.task_summary, fits
        )
        or truncated
    )

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
        agent_thread_id=agent_thread_id(
            request.conversation_key, request.selected_agent_id
        ),
        locale=request.locale,
        token_budget=budget,
        context_truncated=truncated,
    )
    _ensure_projection_within_budget(projection, request, estimator, budget)
    return projection


_REBUILD_FIELDS = frozenset(
    {
        "conversation_key",
        "ledger_entries",
        "artifact_refs",
        "ledger_cursor",
        "ledger_version",
        "observed_mode",
    }
)


def rebuild_business_context(**kwargs: object) -> BusinessContext:
    """Rebuild semantic context from bounded, ordered ledger input."""
    unexpected = set(kwargs) - _REBUILD_FIELDS
    missing = _REBUILD_FIELDS - set(kwargs)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise TypeError(f"unexpected rebuild fields: {names}")
    if missing:
        names = ", ".join(sorted(missing))
        raise TypeError(f"missing rebuild fields: {names}")
    return _rebuild_business_context(
        _RebuildRequest(
            conversation_key=cast(UUID, kwargs["conversation_key"]),
            ledger_entries=cast(
                Sequence[Mapping[str, object]], kwargs["ledger_entries"]
            ),
            artifact_refs=cast(
                Sequence[ArtifactRefV1], kwargs["artifact_refs"]
            ),
            ledger_cursor=cast(int, kwargs["ledger_cursor"]),
            ledger_version=cast(str, kwargs["ledger_version"]),
            observed_mode=cast(
                Literal["instant", "expert"], kwargs["observed_mode"]
            ),
        )
    )


def _rebuild_business_context(request: _RebuildRequest) -> BusinessContext:
    """Rebuild semantic context from bounded, ordered ledger input."""
    recent_turns: list[RoleTaggedTurn] = []

    def bound_text(value: str) -> str:
        """Keep both ends of oversized text while preserving a fixed bound."""
        if len(value) <= MAX_CONTEXT_TEXT_CHARS:
            return value
        prefix = MAX_CONTEXT_TEXT_CHARS // 2
        return value[:prefix] + value[-(MAX_CONTEXT_TEXT_CHARS - prefix) :]

    for raw in request.ledger_entries:
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
        last_applied_ledger_cursor=request.ledger_cursor,
        last_applied_ledger_version=request.ledger_version,
        observed_mode=request.observed_mode,
        recent_turns=recent_turns[-50:],
        artifact_index=list(request.artifact_refs),
        per_agent_memory={
            agent: PerAgentMemory(
                agent_id=agent,
                thread_id=agent_thread_id(request.conversation_key, agent),
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
