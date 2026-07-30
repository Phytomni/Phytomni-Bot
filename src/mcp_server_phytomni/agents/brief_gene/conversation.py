# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded conversation operations for Brief Gene turns.

The Brief Gene graph still owns full report generation.  This adapter only
classifies a context turn, carries the stable private graph thread, and
projects small gene/report metadata into Bot-owned conversation context.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ...common.responses import message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    ContextDelta,
    ContextEntity,
    ContextProjection,
    PerAgentMemory,
)

_MAX_GENE_ID_CHARS = 128
_MAX_SUMMARY_CHARS = 1024
_MAX_EVIDENCE_REF_CHARS = 256
_INVALID_RESPONSE_TEXT = frozenset(
    {
        "no answer generated",
        "no answer generated.",
        "n/a",
        "none",
        "null",
    }
)
_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_.-]{2,127})(?![A-Za-z0-9])"
)
_REFRESH_PATTERN = re.compile(
    r"\b(?:refresh|rerun|re-run|regenerate|rebuild|recalculate|"
    r"run\s+again|latest\s+report|new\s+report|"
    r"(?:update|redo)\s+(?:the|this|my)\s+report)\b",
    re.IGNORECASE,
)
_NEW_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:new|different|another)\s+gene\b|"
    r"\b(?:switch|change)\s+to\s+(?:a\s+)?(?:new\s+)?gene\b",
    re.IGNORECASE,
)
_SPECIES_ALIASES: dict[str, tuple[str, ...]] = {
    "osa": ("osa", "rice", "oryza sativa"),
    "ath": ("ath", "arabidopsis", "arabidopsis thaliana"),
    "zma": ("zma", "maize", "corn", "zea mays"),
    "sbi": ("sbi", "sorghum", "sorghum bicolor"),
    "gma": ("gma", "soybean", "soya", "glycine max"),
    "hvu": ("hvu", "barley", "hordeum vulgare"),
}
_IDENTIFIER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("loc_os", "osa"),
    ("traes", "tad"),
    ("os", "osa"),
    ("at", "ath"),
    ("zm", "zma"),
    ("sb", "sbi"),
    ("gm", "gma"),
    ("hv", "hvu"),
    ("br", "bna"),
    ("sl", "sly"),
    ("bd", "bdi"),
    ("mt", "mtr"),
    ("pt", "ptr"),
)
_BRIEF_GENE_ENTITY_NAMESPACE = "brief_gene."
_GENE_ENTITY_PREFIX = f"{_BRIEF_GENE_ENTITY_NAMESPACE}gene."
_SPECIES_ENTITY_PREFIX = f"{_BRIEF_GENE_ENTITY_NAMESPACE}species."
_EVIDENCE_ENTITY_PREFIX = f"{_BRIEF_GENE_ENTITY_NAMESPACE}evidence."
_REPORT_REVISION_ENTITY_PREFIX = (
    f"{_BRIEF_GENE_ENTITY_NAMESPACE}report_revision."
)
_ARTIFACT_ENTITY_PREFIX = f"{_BRIEF_GENE_ENTITY_NAMESPACE}artifact."


class BriefGeneConversationOperation(StrEnum):
    """Private operation selected for one Brief Gene context turn."""

    NEW_REPORT = "new_report"
    FOLLOW_UP = "follow_up"
    REFRESH = "refresh"
    NEW_IDENTIFIER = "new_identifier"
    CLARIFY = "clarify"


class BriefGeneClarificationError(ValueError):
    """Raised when a Brief Gene turn cannot be safely resolved."""


@dataclass(frozen=True, slots=True)
class BriefGeneActiveContext:
    """Bounded active Brief Gene metadata recovered from Bot context."""

    gene_id: str | None = None
    species_code: str | None = None
    report_summary: str = ""
    evidence_refs: tuple[str, ...] = ()
    artifact_id: str | None = None
    report_revision: int = 0


# A shorter alias keeps call sites readable without creating a second type.
BriefGeneContextSnapshot = BriefGeneActiveContext


@dataclass(frozen=True, slots=True)
class _PreparedBriefGeneTurn:
    projection: ContextProjection
    operation: BriefGeneConversationOperation
    active: BriefGeneActiveContext
    explicit_gene_id: str | None
    explicit_species_code: str | None


ChatSeam = Callable[[str], Awaitable[Mapping[str, Any] | str | None]]


def _bounded_text(value: object, limit: int = MAX_CONTEXT_TEXT_CHARS) -> str:
    """Keep one context text value bounded and whitespace-normalized."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _opaque_text(value: object, limit: int = _MAX_EVIDENCE_REF_CHARS) -> str:
    """Normalize one reference without admitting paths or URI-like values."""
    if not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    if not text or "/" in text or "\\" in text:
        return ""
    return text[:limit]


def _normalize_species(value: object) -> str | None:
    """Normalize a supported species code or common name."""
    if not isinstance(value, str):
        return None
    lowered = " ".join(value.casefold().split())
    if not lowered:
        return None
    for code, aliases in _SPECIES_ALIASES.items():
        if lowered == code or lowered in aliases:
            return code
    return lowered[:32] if re.fullmatch(r"[a-z0-9_-]{2,32}", lowered) else None


def _identifier_species(identifier: str) -> str | None:
    """Infer species from a supported plant-gene identifier prefix."""
    lowered = identifier.casefold().replace("-", "_")
    for prefix, species in _IDENTIFIER_PREFIXES:
        if lowered.startswith(prefix):
            return species
    return None


def _looks_like_gene_identifier(token: str) -> bool:
    """Recognize explicit gene ids without treating prose as identifiers."""
    normalized = token.strip(".,:;()[]{}")
    if len(normalized) < 4 or len(normalized) > _MAX_GENE_ID_CHARS:
        return False
    lowered = normalized.casefold().replace("-", "_")
    known_prefix = _identifier_species(normalized)
    if known_prefix is not None:
        suffix = lowered
        original_suffix = normalized
        for prefix, _species in _IDENTIFIER_PREFIXES:
            if suffix.startswith(prefix):
                suffix = suffix[len(prefix) :]
                original_suffix = original_suffix[len(prefix) :]
                break
        return any(char.isdigit() for char in suffix) or (
            suffix.isalpha() and original_suffix.isupper()
        )
    return bool(re.fullmatch(r"[A-Z]{2,12}[0-9][A-Za-z0-9_-]*", normalized))


def explicit_brief_gene_identifiers(query: str) -> tuple[str, ...]:
    """Return explicit identifier-like tokens in first appearance order."""
    values: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_PATTERN.finditer(query):
        token = match.group(1).strip(".,:;()[]{}")
        if not _looks_like_gene_identifier(token):
            continue
        lowered = token.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(token[:_MAX_GENE_ID_CHARS])
    return tuple(values)


def _species_mentions(query: str) -> tuple[str, ...]:
    """Return distinct species names explicitly mentioned in a query."""
    lowered = " ".join(query.casefold().split())
    found: list[str] = []
    for code, aliases in _SPECIES_ALIASES.items():
        for alias in aliases:
            pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
            if re.search(pattern, lowered):
                found.append(code)
                break
    return tuple(dict.fromkeys(found))


def _active_from_projection(
    projection: ContextProjection,
) -> BriefGeneActiveContext:
    """Recover only the bounded Brief Gene entity namespace."""
    gene_id: str | None = None
    species_code: str | None = None
    evidence: list[str] = []
    artifact_id: str | None = None
    report_revision = 0
    for entity in projection.active_entities:
        if entity.entity_id.startswith(_GENE_ENTITY_PREFIX):
            candidate = (
                entity.label or entity.entity_id[len(_GENE_ENTITY_PREFIX) :]
            )
            if _looks_like_gene_identifier(candidate):
                gene_id = candidate[:_MAX_GENE_ID_CHARS]
        elif entity.entity_id.startswith(_SPECIES_ENTITY_PREFIX):
            species_code = _normalize_species(entity.label)
            if species_code is None:
                species_code = _normalize_species(
                    entity.entity_id[len(_SPECIES_ENTITY_PREFIX) :]
                )
        elif entity.entity_id.startswith(_EVIDENCE_ENTITY_PREFIX):
            value = _opaque_text(
                entity.label
                or entity.entity_id[len(_EVIDENCE_ENTITY_PREFIX) :]
            )
            if value and value not in evidence:
                evidence.append(value)
        elif entity.entity_id.startswith(_ARTIFACT_ENTITY_PREFIX):
            value = _opaque_text(
                entity.label
                or entity.entity_id[len(_ARTIFACT_ENTITY_PREFIX) :],
                128,
            )
            if value:
                artifact_id = value
        elif entity.entity_id.startswith(_REPORT_REVISION_ENTITY_PREFIX):
            suffix = entity.entity_id[len(_REPORT_REVISION_ENTITY_PREFIX) :]
            if suffix.isdigit():
                report_revision = max(0, int(suffix))
    if artifact_id is None and len(projection.artifact_refs) == 1:
        artifact_id = projection.artifact_refs[0].artifact_id
    return BriefGeneActiveContext(
        gene_id=gene_id,
        species_code=species_code,
        report_summary=_bounded_text(
            projection.task_summary, _MAX_SUMMARY_CHARS
        ),
        evidence_refs=tuple(evidence[:MAX_CONTEXT_ITEMS]),
        artifact_id=artifact_id,
        report_revision=report_revision,
    )


def _coerce_active_context(
    value: BriefGeneActiveContext | Mapping[str, Any] | None,
) -> BriefGeneActiveContext:
    """Accept the internal dataclass or a durable mapping representation."""
    if value is None:
        return BriefGeneActiveContext()
    if isinstance(value, BriefGeneActiveContext):
        return value
    refs = value.get("evidence_refs", ())
    if isinstance(refs, str) or not isinstance(refs, Sequence):
        refs = ()
    bounded_refs = tuple(
        item for item in (_opaque_text(ref) for ref in refs) if item
    )[:MAX_CONTEXT_ITEMS]
    revision = value.get("report_revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int):
        revision = 0
    return BriefGeneActiveContext(
        gene_id=_opaque_text(value.get("gene_id"), _MAX_GENE_ID_CHARS) or None,
        species_code=_normalize_species(value.get("species_code")),
        report_summary=_bounded_text(
            value.get("report_summary"), _MAX_SUMMARY_CHARS
        ),
        evidence_refs=bounded_refs,
        artifact_id=_opaque_text(value.get("artifact_id"), 128) or None,
        report_revision=max(0, revision),
    )


def _classify_active_brief_gene_turn(
    active: BriefGeneActiveContext,
    identifiers: Sequence[str],
    mentioned_species: Sequence[str],
    text: str,
) -> BriefGeneConversationOperation:
    """Apply the ordered operation rules once input is normalized."""
    inferred_species = (
        _identifier_species(identifiers[0]) if len(identifiers) == 1 else None
    )
    refresh = bool(_REFRESH_PATTERN.search(text))
    operation: BriefGeneConversationOperation
    if _requires_brief_gene_clarification(
        identifiers, mentioned_species, inferred_species, text
    ):
        operation = BriefGeneConversationOperation.CLARIFY
    elif active.gene_id is None:
        operation = (
            BriefGeneConversationOperation.NEW_REPORT
            if len(identifiers) == 1
            else BriefGeneConversationOperation.CLARIFY
        )
    elif (
        identifiers and identifiers[0].casefold() != active.gene_id.casefold()
    ):
        operation = BriefGeneConversationOperation.NEW_IDENTIFIER
    else:
        active_species = active.species_code or _identifier_species(
            active.gene_id
        )
        species_changed = (
            not identifiers
            and len(mentioned_species) == 1
            and active_species is not None
            and mentioned_species[0] != active_species
        )
        if species_changed:
            operation = BriefGeneConversationOperation.CLARIFY
        elif refresh:
            operation = BriefGeneConversationOperation.REFRESH
        else:
            operation = BriefGeneConversationOperation.FOLLOW_UP
    return operation


def _requires_brief_gene_clarification(
    identifiers: Sequence[str],
    mentioned_species: Sequence[str],
    inferred_species: str | None,
    text: str,
) -> bool:
    """Return whether normalized identifiers require a clarification turn."""
    multiple_values = len(identifiers) > 1 or len(mentioned_species) > 1
    species_conflict = bool(
        inferred_species
        and mentioned_species
        and inferred_species not in mentioned_species
    )
    missing_identifier = bool(_NEW_IDENTIFIER_PATTERN.search(text)) and not (
        identifiers
    )
    return multiple_values or species_conflict or missing_identifier


def classify_brief_gene_operation(
    query: str | ContextProjection,
    *,
    active_context: BriefGeneActiveContext | Mapping[str, Any] | None = None,
    active_gene_id: str | None = None,
    active_species_code: str | None = None,
) -> BriefGeneConversationOperation:
    """Classify a turn deterministically before resolver or graph calls."""
    if isinstance(query, ContextProjection):
        projection = query
        text = query.current_query
    else:
        projection = None
        text = query
    active = _coerce_active_context(active_context)
    if projection is not None and active_context is None:
        active = _active_from_projection(projection)
    if active_gene_id is not None:
        active = BriefGeneActiveContext(
            gene_id=active_gene_id,
            species_code=active_species_code or active.species_code,
            report_summary=active.report_summary,
            evidence_refs=active.evidence_refs,
            artifact_id=active.artifact_id,
            report_revision=active.report_revision,
        )
    return _classify_active_brief_gene_turn(
        active,
        explicit_brief_gene_identifiers(text),
        _species_mentions(text),
        text,
    )


def _answer_text(value: object) -> str:
    """Read the current public answer, never a stale private state document."""
    if isinstance(value, Mapping):
        answer = message_content(value)
        if answer.strip():
            return answer.strip()
        nested = value.get("result")
        if isinstance(nested, Mapping):
            formatted = nested.get("formatted")
            if isinstance(formatted, Mapping):
                formatted_answer = formatted.get("answer")
                if (
                    isinstance(formatted_answer, str)
                    and formatted_answer.strip()
                ):
                    return formatted_answer.strip()
            raw = nested.get("raw")
            if isinstance(raw, Mapping):
                answer = message_content(raw)
                if answer.strip():
                    return answer.strip()
    return ""


def _usable_answer(value: str) -> bool:
    """Reject empty and placeholder public answers."""
    return (
        bool(value.strip())
        and value.strip().casefold() not in _INVALID_RESPONSE_TEXT
    )


def _state_mapping(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the non-public state metadata attached to a result."""
    state = result.get("phytomni_state")
    if isinstance(state, Mapping):
        return state
    nested = result.get("result")
    if isinstance(nested, Mapping) and isinstance(
        nested.get("phytomni_state"), Mapping
    ):
        return nested["phytomni_state"]
    return {}


def _ordered_documents(
    value: object, *, depth: int = 0
) -> list[dict[str, Any]]:
    """Find ordered reference metadata without copying document bodies."""
    if depth > 3 or not isinstance(value, Mapping):
        return []
    for key in (
        "doc_list",
        "ordered_doc_list",
        "references",
        "retrieved_docs",
    ):
        raw = value.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            documents = [
                dict(item) for item in raw if isinstance(item, Mapping)
            ][:MAX_CONTEXT_ITEMS]
            if documents:
                return documents
    for key in (
        "result",
        "choices",
        "message",
        "phytomni_state",
        "raw",
        "formatted",
    ):
        nested = value.get(key)
        if isinstance(nested, Sequence) and not isinstance(
            nested, (str, bytes)
        ):
            for item in nested:
                found = _ordered_documents(item, depth=depth + 1)
                if found:
                    return found
        else:
            found = _ordered_documents(nested, depth=depth + 1)
            if found:
                return found
    return []


def _document_reference(document: Mapping[str, Any]) -> str:
    """Extract one opaque evidence reference from a document row."""
    for key in (
        "file_id",
        "source_id",
        "doc_id",
        "sourceId",
        "reference_id",
        "id",
        "pmid",
        "doi",
    ):
        value = _opaque_text(document.get(key))
        if value:
            return value
    return ""


def _summary_from_answer(answer: str) -> str:
    """Reduce a report answer to a short context summary."""
    lines = [
        line.strip(" #\t") for line in answer.splitlines() if line.strip()
    ]
    if lines and lines[0].casefold().startswith("brief gene analysis"):
        lines = lines[1:]
    summary = lines[0] if lines else answer
    return _bounded_text(summary, _MAX_SUMMARY_CHARS)


def _semantic_id(prefix: str, value: str, *, limit: int = 128) -> str:
    """Make one deterministic bounded context entity id."""
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").casefold()
    return f"{prefix}{normalized}"[:limit]


def _clarification_result(message: str) -> dict[str, Any]:
    """Shape one bounded clarification in the legacy chat envelope."""
    return {
        "choices": [
            {
                "message": {
                    "content": _bounded_text(message, _MAX_SUMMARY_CHARS),
                    "doc_list": [],
                    "total": 10000,
                    "follow_up_questions": [],
                }
            }
        ]
    }


class BriefGeneConversationAdapter:
    """Prepare bounded Brief Gene operations and context deltas."""

    def __init__(self) -> None:
        self._prepared: _PreparedBriefGeneTurn | None = None
        self._active: BriefGeneActiveContext = BriefGeneActiveContext()
        self._candidate: BriefGeneActiveContext | None = None
        self._captured_result: dict[str, Any] = {}
        self._operation_successful = False
        self._evidence_documents: list[dict[str, Any]] = []

    def prepare(
        self,
        projection: ContextProjection,
        *,
        active_context: (
            BriefGeneActiveContext | Mapping[str, Any] | None
        ) = None,
    ) -> dict[str, Any]:
        """Classify and prepare one context turn without expensive calls."""
        active = (
            _coerce_active_context(active_context)
            if active_context is not None
            else _active_from_projection(projection)
        )
        operation = classify_brief_gene_operation(
            projection,
            active_context=active,
        )
        identifiers = explicit_brief_gene_identifiers(projection.current_query)
        explicit_gene_id = identifiers[0] if len(identifiers) == 1 else None
        explicit_species = (
            _identifier_species(explicit_gene_id)
            if explicit_gene_id is not None
            else None
        )
        self._prepared = _PreparedBriefGeneTurn(
            projection=projection,
            operation=operation,
            active=active,
            explicit_gene_id=explicit_gene_id,
            explicit_species_code=explicit_species,
        )
        self._active = active
        self._candidate = None
        self._captured_result = {}
        self._operation_successful = operation not in {
            BriefGeneConversationOperation.CLARIFY,
        }
        self._evidence_documents = [
            {"file_id": reference} for reference in active.evidence_refs
        ]
        return {
            "user_query": projection.current_query,
            "locale": projection.locale,
            "thread_id": projection.agent_thread_id,
            "operation": operation,
            "gene_id": (
                explicit_gene_id
                if operation
                in {
                    BriefGeneConversationOperation.NEW_REPORT,
                    BriefGeneConversationOperation.NEW_IDENTIFIER,
                }
                else active.gene_id
            ),
            "species_code": explicit_species or active.species_code,
            "resolver_query": self.resolver_query,
            "report_summary": active.report_summary,
            "evidence_refs": active.evidence_refs,
            "artifact_id": active.artifact_id,
            "report_revision": active.report_revision,
            "clarification_message": self.clarification_message,
        }

    @property
    def operation(self) -> BriefGeneConversationOperation | None:
        """Return the prepared operation."""
        return self._prepared.operation if self._prepared else None

    @property
    def thread_id(self) -> str | None:
        """Return the stable private graph thread from the projection."""
        return (
            self._prepared.projection.agent_thread_id
            if self._prepared
            else None
        )

    @property
    def active_context(self) -> BriefGeneActiveContext:
        """Return the last successful active metadata."""
        return self._active

    @property
    def active_gene_id(self) -> str | None:
        """Return the active gene identifier, if any."""
        return self._active.gene_id

    @property
    def active_species_code(self) -> str | None:
        """Return the active species code, if any."""
        return self._active.species_code

    @property
    def report_revision(self) -> int:
        """Return the last successful report revision."""
        return self._active.report_revision

    @property
    def settlement_ready(self) -> bool:
        """Return whether the current operation produced usable output."""
        return self._operation_successful

    @property
    def resolver_query(self) -> str | None:
        """Return only an identifier-safe resolver input."""
        if self._prepared is None:
            return None
        operation = self._prepared.operation
        if operation is BriefGeneConversationOperation.REFRESH:
            return self._active.gene_id
        if operation in {
            BriefGeneConversationOperation.NEW_REPORT,
            BriefGeneConversationOperation.NEW_IDENTIFIER,
        }:
            return self._prepared.explicit_gene_id
        return None

    @property
    def clarification_message(self) -> str:
        """Return a bounded clarification for unresolved operations."""
        return (
            "Please provide one unambiguous supported gene identifier "
            "and species."
        )

    def mark_failed(self) -> None:
        """Make failed or incomplete output non-stageable."""
        self._operation_successful = False
        self._candidate = None

    def follow_up_prompt(self) -> str:
        """Build a bounded prompt from active metadata, not the full report."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before follow_up_prompt")
        active = self._active
        evidence = ", ".join(active.evidence_refs) or "none"
        return (
            "Answer the Brief Gene follow-up using only "
            "the bounded active context. "
            "Do not regenerate or quote the complete report.\n\n"
            f"[active gene]\n{active.gene_id or 'unknown'}\n\n"
            f"[species]\n{active.species_code or 'unknown'}\n\n"
            f"[report summary]\n{active.report_summary or 'none'}\n\n"
            f"[authorized evidence refs]\n{evidence}\n\n"
            f"[current question]\n"
            f"{_bounded_text(self._prepared.projection.current_query)}"
        )[:MAX_CONTEXT_TEXT_CHARS]

    async def follow_up(self, chat: ChatSeam) -> dict[str, Any]:
        """Answer one conversational follow-up without the full graph."""
        if (
            self._prepared is None
            or self.operation is not BriefGeneConversationOperation.FOLLOW_UP
        ):
            raise BriefGeneClarificationError(
                "No active Brief Gene report is available."
            )
        response = await chat(self.follow_up_prompt())
        answer = _answer_text(response)
        if not _usable_answer(answer):
            self.mark_failed()
            raise BriefGeneClarificationError(
                "Brief Gene follow-up returned empty or invalid content."
            )
        self._operation_successful = True
        self._captured_result = self._answer_result(answer)
        return self._captured_result

    def capture_result(
        self,
        result: Mapping[str, Any],
        *,
        resolved: object | None = None,
    ) -> bool:
        """Capture current public output and replace active metadata
        on success."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before capture_result")
        answer = _answer_text(result)
        if not _usable_answer(answer):
            self.mark_failed()
            return False
        state = _state_mapping(result)
        resolved_gene = getattr(resolved, "gene_id", None)
        resolved_species = getattr(resolved, "species_code", None)
        if isinstance(resolved, Mapping):
            resolved_gene = resolved.get("gene_id", resolved_gene)
            resolved_species = resolved.get("species_code", resolved_species)
        gene_id = _opaque_text(
            state.get("gene_id") or resolved_gene or self._active.gene_id,
            _MAX_GENE_ID_CHARS,
        )
        species_code = _normalize_species(
            state.get("species_code")
            or resolved_species
            or self._active.species_code
        )
        if not gene_id or species_code is None:
            self.mark_failed()
            return False
        if self.operation is BriefGeneConversationOperation.FOLLOW_UP:
            self._captured_result = dict(result)
            return True
        artifact_id = (
            _opaque_text(
                state.get("report_artifact_id")
                or state.get("artifact_id")
                or self._active.artifact_id,
                128,
            )
            or None
        )
        revision = state.get(
            "report_revision", self._active.report_revision + 1
        )
        if isinstance(revision, bool) or not isinstance(revision, int):
            revision = self._active.report_revision + 1
        documents = _ordered_documents(result)
        refs = tuple(
            reference
            for reference in (_document_reference(item) for item in documents)
            if reference
        )[:MAX_CONTEXT_ITEMS]
        candidate = BriefGeneActiveContext(
            gene_id=gene_id,
            species_code=species_code,
            report_summary=(
                _bounded_text(
                    state.get("report_summary") or state.get("summary"),
                    _MAX_SUMMARY_CHARS,
                )
                or _summary_from_answer(answer)
            ),
            evidence_refs=refs,
            artifact_id=artifact_id,
            report_revision=max(0, revision),
        )
        self._candidate = candidate
        self._active = candidate
        self._evidence_documents = [
            {"file_id": reference} for reference in refs
        ]
        self._captured_result = dict(result)
        self._operation_successful = True
        return True

    def delta(self, result: Mapping[str, Any] | None = None) -> ContextDelta:
        """Project bounded gene/report metadata into a ContextDelta."""
        del result
        if self._prepared is None:
            raise RuntimeError("prepare must run before delta")
        if not self._operation_successful:
            return ContextDelta()
        active = self._active
        if active.gene_id is None or active.species_code is None:
            return ContextDelta()
        entities = [
            ContextEntity(
                entity_id=_semantic_id(_GENE_ENTITY_PREFIX, active.gene_id),
                entity_type="gene",
                label=active.gene_id,
            ),
            ContextEntity(
                entity_id=_semantic_id(
                    _SPECIES_ENTITY_PREFIX, active.species_code
                ),
                entity_type="species",
                label=active.species_code,
            ),
            ContextEntity(
                entity_id=_semantic_id(
                    _REPORT_REVISION_ENTITY_PREFIX, str(active.report_revision)
                ),
                entity_type="task",
                label=f"report revision {active.report_revision}",
            ),
        ]
        for reference in active.evidence_refs[:MAX_CONTEXT_ITEMS]:
            entities.append(
                ContextEntity(
                    entity_id=_semantic_id(_EVIDENCE_ENTITY_PREFIX, reference),
                    entity_type="task",
                    label=reference[:MAX_CONTEXT_TEXT_CHARS],
                )
            )
        authorized_artifacts = {
            item.artifact_id: item
            for item in self._prepared.projection.artifact_refs
        }
        artifact_upserts: list[ArtifactRefV1] = []
        if active.artifact_id in authorized_artifacts:
            artifact_upserts.append(authorized_artifacts[active.artifact_id])
            entities.append(
                ContextEntity(
                    entity_id=_semantic_id(
                        _ARTIFACT_ENTITY_PREFIX, active.artifact_id
                    ),
                    entity_type="file",
                    label=active.artifact_id,
                )
            )
        current_ids = {entity.entity_id for entity in entities}
        removals = [
            entity.entity_id
            for entity in self._prepared.projection.active_entities
            if entity.entity_id.startswith(_BRIEF_GENE_ENTITY_NAMESPACE)
            and entity.entity_id not in current_ids
        ][:MAX_CONTEXT_ITEMS]
        summary = active.report_summary[:MAX_CONTEXT_TEXT_CHARS]
        return ContextDelta(
            summary_update=summary or None,
            entity_upserts=entities[:MAX_CONTEXT_ITEMS],
            entity_removals=removals,
            artifact_upserts=artifact_upserts,
            agent_memory_update=PerAgentMemory(
                agent_id="BriefGeneAgent",
                thread_id=self._prepared.projection.agent_thread_id,
                summary=summary,
                checkpoint_ref=(
                    active.artifact_id
                    if active.artifact_id in authorized_artifacts
                    else None
                ),
            ),
        )

    def _answer_result(self, content: str) -> dict[str, Any]:
        """Shape a bounded follow-up answer with active evidence references."""
        documents = self._evidence_documents or [
            {"file_id": reference} for reference in self._active.evidence_refs
        ]
        return {
            "choices": [
                {
                    "message": {
                        "content": _bounded_text(content),
                        "doc_list": [
                            dict(item)
                            for item in documents[:MAX_CONTEXT_ITEMS]
                        ],
                        "total": 10000,
                        "follow_up_questions": [],
                    }
                }
            ]
        }


def brief_gene_clarification_result(message: str) -> dict[str, Any]:
    """Return a public clarification without exposing private context."""
    return _clarification_result(message)


__all__ = [
    "BriefGeneActiveContext",
    "BriefGeneClarificationError",
    "BriefGeneContextSnapshot",
    "BriefGeneConversationAdapter",
    "BriefGeneConversationOperation",
    "brief_gene_clarification_result",
    "classify_brief_gene_operation",
    "explicit_brief_gene_identifiers",
]
