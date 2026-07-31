# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Knowledge-specific preparation for V1 conversation context turns."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...common.responses import join_limited_fragments, message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_TEXT_CHARS,
    ContextDelta,
    ContextEntity,
    ContextProjection,
    PerAgentMemory,
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,}")
_PRONOUN_PATTERN = re.compile(
    r"\b(that|this|it|its|they|them|their|those|these)\b", re.IGNORECASE
)
_EVIDENCE_PATTERN = re.compile(
    r"(?i)\bwhat evidence supports\b\s+(?:that|this|it)\b"
)


class KnowledgeClarificationError(ValueError):
    """Raised when a follow-up lacks a resolvable subject entity."""


@dataclass(slots=True)
class _PreparedKnowledgeTurn:
    user_query: str
    retrieval_query: str
    answer_context: str
    thread_id: str
    topic_label: str | None
    topic_entity_removals: tuple[str, ...]


def _bounded_text(value: str | None) -> str:
    """Clamp context text to the shared semantic bound."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:MAX_CONTEXT_TEXT_CHARS]


def _normalize_space(value: str) -> str:
    """Collapse incidental whitespace without changing identifiers."""
    return " ".join(value.split())


def _candidate_tokens(text: str) -> list[str]:
    """Return identifier-like tokens in first appearance order."""
    candidates: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_PATTERN.findall(text):
        lowered = token.lower()
        if lowered in seen:
            continue
        if not any(char.isdigit() for char in token):
            continue
        candidates.append(token)
        seen.add(lowered)
    return candidates


def _topic_candidates(projection: ContextProjection) -> list[str]:
    """Collect likely topical entities from active state and recent turns."""
    ordered: list[str] = []
    seen: set[str] = set()

    def admit(label: str) -> None:
        normalized = label.strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        ordered.append(normalized)

    for entity in projection.active_entities:
        admit(entity.label)
    for turn in reversed(projection.relevant_recent_turns):
        for token in _candidate_tokens(turn.content):
            admit(token)
    return ordered


def _explicit_topic(
    query: str, *, candidates: Sequence[str]
) -> tuple[str | None, bool]:
    """Return an explicit entity label and whether it is newly introduced."""
    lowered_query = query.lower()
    for candidate in candidates:
        if candidate.lower() in lowered_query:
            return candidate, False
    tokens = _candidate_tokens(query)
    if tokens:
        token = tokens[0]
        return token, token.lower() not in {
            item.lower() for item in candidates
        }
    return None, False


def _standalone_query(query: str, topic: str) -> str:
    """Resolve a pronoun-heavy follow-up into a standalone retrieval query."""
    if _EVIDENCE_PATTERN.search(query):
        return _EVIDENCE_PATTERN.sub(
            f"What evidence supports {topic}", query, count=1
        )
    if _PRONOUN_PATTERN.search(query):
        return f"{query.rstrip('?.!')} about {topic}?"
    return query


def _answer_context_fragments(
    projection: ContextProjection,
) -> tuple[str, ...]:
    """Render bounded context fragments for generation-only prompt use."""
    fragments: list[str] = []
    recent_turns = projection.relevant_recent_turns
    for index, turn in enumerate(recent_turns, start=1):
        role = turn.role
        content = _bounded_text(turn.content)
        if content:
            fragments.append(f"[recent turn {index}]\n{role}: {content}")
    if not fragments and projection.task_summary:
        fragments.append(
            f"[task summary]\n{_bounded_text(projection.task_summary)}"
        )
    if projection.active_entities:
        labels = ", ".join(item.label for item in projection.active_entities)
        fragments.append(f"[active entities]\n{_bounded_text(labels)}")
    if projection.open_questions:
        fragments.append(
            "[open questions]\n"
            + "\n".join(
                f"- {_bounded_text(question)}"
                for question in projection.open_questions
            )
        )
    return tuple(fragments)


def _answer_context(projection: ContextProjection) -> str:
    """Build one bounded generation context string."""
    joined, _ = join_limited_fragments(
        _answer_context_fragments(projection),
        max_tokens=MAX_CONTEXT_TEXT_CHARS,
    )
    return joined


def _answer_text(result: Mapping[str, Any]) -> str:
    """Extract the best bounded answer text from a raw or wrapped result."""
    payload = result.get("result")
    if isinstance(payload, Mapping):
        formatted = payload.get("formatted")
        if isinstance(formatted, Mapping):
            answer = formatted.get("answer")
            if isinstance(answer, str) and answer.strip():
                return _bounded_text(answer)
        raw = payload.get("raw")
        if isinstance(raw, Mapping):
            answer = message_content(raw)
            if answer.strip():
                return _bounded_text(answer)
    answer = message_content(result)
    return _bounded_text(answer)


def _follow_up_questions(result: Mapping[str, Any]) -> list[str]:
    """Extract bounded follow-up questions from a raw or wrapped result."""
    payload = result.get("result")
    if isinstance(payload, Mapping):
        formatted = payload.get("formatted")
        if isinstance(formatted, Mapping):
            questions = formatted.get("follow_up_questions")
            if isinstance(questions, Sequence) and not isinstance(
                questions, str
            ):
                return [
                    _bounded_text(str(question))
                    for question in questions
                    if isinstance(question, str) and question.strip()
                ]
        raw = payload.get("raw")
        if isinstance(raw, Mapping):
            message = raw.get("choices")
            if (
                isinstance(message, Sequence)
                and message
                and isinstance(message[0], Mapping)
            ):
                first = message[0].get("message")
                if isinstance(first, Mapping):
                    questions = first.get("follow_up_questions")
                    if isinstance(questions, Sequence) and not isinstance(
                        questions, str
                    ):
                        return [
                            _bounded_text(str(question))
                            for question in questions
                            if isinstance(question, str) and question.strip()
                        ]
    return []


def _topic_entity(label: str) -> ContextEntity:
    """Project the active Knowledge topic into one bounded semantic entity."""
    entity_id = ("knowledge:" + label.lower().replace(" ", "-"))[:128]
    return ContextEntity(entity_id=entity_id, entity_type="gene", label=label)


def _topic_entity_removals(
    projection: ContextProjection,
    *,
    topic_label: str | None,
    is_new_topic: bool,
) -> tuple[str, ...]:
    """Return prior Knowledge-topic ids to drop after a successful switch."""
    if topic_label is None or not is_new_topic:
        return ()
    removals: list[str] = []
    lowered_topic = topic_label.lower()
    for entity in projection.active_entities:
        if entity.entity_type != "gene":
            continue
        if entity.label.lower() == lowered_topic:
            continue
        removals.append(entity.entity_id)
    return tuple(removals)


class KnowledgeConversationAdapter:
    """Prepare Knowledge V1 turns without exposing transcripts to retrieval."""

    def __init__(self) -> None:
        self._prepared: _PreparedKnowledgeTurn | None = None

    def prepare(self, projection: ContextProjection) -> dict[str, str]:
        """Resolve one retrieval query and bounded answer context."""
        query = _normalize_space(projection.current_query)
        candidates = _topic_candidates(projection)
        topic_label, is_new_topic = _explicit_topic(
            query, candidates=candidates
        )
        if topic_label is None and _PRONOUN_PATTERN.search(query):
            if not candidates:
                raise KnowledgeClarificationError(
                    "Please clarify the target requiring evidence."
                )
            topic_label = candidates[0]
        retrieval_query = (
            _standalone_query(query, topic_label)
            if topic_label is not None
            else query
        )
        prepared = _PreparedKnowledgeTurn(
            user_query=query,
            retrieval_query=_normalize_space(retrieval_query),
            answer_context=_answer_context(projection),
            thread_id=projection.agent_thread_id,
            topic_label=topic_label,
            topic_entity_removals=_topic_entity_removals(
                projection,
                topic_label=topic_label,
                is_new_topic=is_new_topic,
            ),
        )
        self._prepared = prepared
        return {
            "user_query": prepared.user_query,
            "retrieval_query": prepared.retrieval_query,
            "answer_context": prepared.answer_context,
            "thread_id": prepared.thread_id,
        }

    def delta(self, result: Mapping[str, Any]) -> ContextDelta:
        """Convert a successful Knowledge answer into bounded deltas."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before delta")
        answer = _answer_text(result)
        topic = self._prepared.topic_label
        return ContextDelta(
            summary_update=answer or None,
            entity_upserts=[] if topic is None else [_topic_entity(topic)],
            entity_removals=list(self._prepared.topic_entity_removals),
            open_question_updates=_follow_up_questions(result),
            agent_memory_update=PerAgentMemory(
                agent_id="KnowledgeAgent",
                thread_id=self._prepared.thread_id,
                summary=answer,
            ),
        )


__all__ = [
    "KnowledgeClarificationError",
    "KnowledgeConversationAdapter",
]
