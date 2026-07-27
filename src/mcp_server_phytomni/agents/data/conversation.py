# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-specific intent preparation for V1 conversation context turns."""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...runtime.conversation_context.models import (
    ArtifactRefV1,
    ContextDelta,
    ContextEntity,
    ContextProjection,
)

_SPACE_RE = re.compile(r"\s+")
_GROUP_BY_RE = re.compile(
    r"\bgroup\s+by\s+([a-z0-9_, -]+?)(?=$|\b(?:for|where|only)\b)",
    re.IGNORECASE,
)
_BY_RE = re.compile(
    r"\bby\s+([a-z0-9_, -]+?)(?=$|\b(?:for|where|only)\b)",
    re.IGNORECASE,
)
_ONLY_RE = re.compile(r"\bonly\s+([a-z0-9_-]+)\b", re.IGNORECASE)
_FOR_RE = re.compile(r"\bfor\s+([a-z0-9_-]+)\b", re.IGNORECASE)
_DATASET_TOKENS = ("expression",)
_DATA_NAMESPACE = "data"
_MAX_INTENT_ITEMS = 8
_MAX_INTENT_COLUMNS = 16
_IntentToken = Annotated[str, Field(min_length=1, max_length=128)]
_SUMMARY_SQL_RE = re.compile(
    r"\b(?:select|insert|update|delete|drop|alter|create|truncate|grant)\b",
    re.IGNORECASE,
)
_SUMMARY_DSN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb|redis|amqp)://",
    re.IGNORECASE,
)
_SUMMARY_ROW_RE = re.compile(
    r"(?:\[[^\]]+,\s*[^\]]+\]|\{[^{}:]+:\s*[^{}]+\}|\|[^\n]+\|)",
    re.IGNORECASE,
)
# Keep the machine-field vocabulary bounded so ordinary scientific prose can
# contain ``secret`` without being treated as a credential.
_SUMMARY_SECRET_FIELD_RE = re.compile(
    r"""
    (?:
        (?<![a-z0-9])
        (?:
            pass(?:word|wd|phrase)?
            |token
            |credential(?:s)?
            |authorization
            |bearer
            |secret
        )
        |
        (?<![a-z0-9])
        (?:
            db
            |database
            |prior[ _-]+session
            |session
            |refresh
            |access
            |credential
            |authorization
            |bearer
            |api
            |private
            |secret
            |x[ _-]?auth
        )
        [ _-]+
        (?:
            pass(?:word|wd|phrase)?
            |token
            |credential(?:s)?
            |authorization
            |bearer
            |secret
            |access[ _-]+key(?:[ _-]+id)?
            |key(?:[ _-]+id)?
            |header
            |ref(?:erence)?
            |id
            |value
            |name
        )
    )
    (?=\s*(?:=|:))
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SUMMARY_SECRET_NAME_RE = re.compile(
    r"""
    (?<![a-z0-9])
    (?:
        db
        |database
        |prior[-_]session
        |session
        |refresh
        |access
        |credential
        |authorization
        |bearer
        |api
        |private
        |secret
        |x[-_]auth
    )
    [-_]+
    (?:
        pass(?:word|wd|phrase)?
        |token
        |credential(?:s)?
        |authorization
        |bearer
        |secret
        |access[-_]key(?:[-_]id)?
        |key(?:[-_]id)?
        |header
        |ref(?:erence)?
        |id
        |value
        |name
    )
    (?![a-z0-9])
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SUMMARY_SECRET_PHRASE_RE = re.compile(
    r"""
    (?:
        \b(?:api|access|private|credential|secret|x[ _-]?auth)
          [ _-]+
          (?:access[ _-]+)?
          (?:key(?:[ _-]+id)?|token|header|ref(?:erence)?)\b
        |\bauthorization\s*:\s*bearer\b
        |\bbearer\s+[a-z0-9._-]+\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


class _StructuredIntent(BaseModel):
    """Validated bounded semantics for one Data conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_ids: tuple[_IntentToken, ...] = Field(
        default=(), max_length=_MAX_INTENT_ITEMS
    )
    table_ids: tuple[_IntentToken, ...] = Field(
        default=(), max_length=_MAX_INTENT_ITEMS
    )
    dimensions: tuple[_IntentToken, ...] = Field(
        default=(), max_length=_MAX_INTENT_ITEMS
    )
    filters: tuple[_IntentToken, ...] = Field(
        default=(), max_length=_MAX_INTENT_ITEMS
    )
    columns: tuple[_IntentToken, ...] = Field(
        default=(), max_length=_MAX_INTENT_COLUMNS
    )
    row_count: int | None = Field(default=None, ge=0, le=1_000_000_000)
    aggregate_summary: str | None = Field(default=None, max_length=1024)
    artifact_id: str | None = Field(default=None, max_length=128)


@dataclass(frozen=True, slots=True)
class _PreparedDataTurn:
    standalone_query: str
    dialog_id: str
    thread_id: str
    prior_entity_ids: tuple[str, ...]
    intent: _StructuredIntent


@dataclass(frozen=True, slots=True)
class _DataResultMetadata:
    dataset_ids: tuple[str, ...] = ()
    table_ids: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    row_count: int | None = None
    aggregate_summary: str | None = None
    artifact_upserts: tuple[ArtifactRefV1, ...] = ()


def _bounded_text(value: str | None, *, limit: int = 512) -> str | None:
    """Trim free text to a small semantic bound."""
    if not isinstance(value, str):
        return None
    text = _SPACE_RE.sub(" ", value).strip()
    if not text:
        return None
    return text[:limit]


def _validated_text(value: str | None, *, limit: int = 512) -> str | None:
    """Normalize text and reject oversized values instead of truncating."""
    if not isinstance(value, str):
        return None
    text = _SPACE_RE.sub(" ", value).strip()
    if not text or len(text) > limit:
        return None
    return text


def _normalize_token(value: str) -> str:
    """Return one lowercase semantic token."""
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")


def _normalize_list(
    values: Sequence[str],
    *,
    limit: int = _MAX_INTENT_ITEMS,
) -> tuple[str, ...]:
    """Dedupe ordered semantic string values."""
    ordered: OrderedDict[str, str] = OrderedDict()
    for value in values:
        bounded = _bounded_text(value, limit=128)
        if bounded is None:
            continue
        lowered = bounded.lower()
        if lowered not in ordered:
            ordered[lowered] = bounded
        if len(ordered) >= limit:
            break
    return tuple(ordered.values())


def _validated_list(
    values: Sequence[str],
    *,
    limit: int = _MAX_INTENT_ITEMS,
) -> tuple[str, ...] | None:
    """Dedupe ordered strings but reject overflow or oversized items."""
    ordered: OrderedDict[str, str] = OrderedDict()
    for value in values:
        bounded = _validated_text(value, limit=128)
        if bounded is None:
            return None
        lowered = bounded.lower()
        if lowered not in ordered:
            ordered[lowered] = bounded
        if len(ordered) > limit:
            return None
    return tuple(ordered.values())


def _validated_aggregate_summary(value: object) -> str | None:
    """Accept only one bounded aggregate narrative, never raw rows or SQL."""
    summary = _validated_text(value, limit=1024) if isinstance(value, str) else None
    if summary is None:
        return None
    if _SUMMARY_SQL_RE.search(summary):
        return None
    if _SUMMARY_DSN_RE.search(summary):
        return None
    if _SUMMARY_ROW_RE.search(summary):
        return None
    if _SUMMARY_SECRET_FIELD_RE.search(summary):
        return None
    if _SUMMARY_SECRET_NAME_RE.search(summary):
        return None
    if _SUMMARY_SECRET_PHRASE_RE.search(summary):
        return None
    return summary


def _dataset_from_query(query: str) -> str | None:
    """Infer one coarse dataset identifier from the current query."""
    lowered = query.lower()
    for token in _DATASET_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return token
    return None


def _split_dimensions(match_text: str | None) -> tuple[str, ...]:
    """Parse one dimension list from a ``by`` clause."""
    if not isinstance(match_text, str):
        return ()
    raw = re.split(
        r"\s*(?:,| and )\s*",
        match_text.strip(),
        flags=re.IGNORECASE,
    )
    return _normalize_list(raw)


def _query_dimensions(query: str) -> tuple[str, ...] | None:
    """Return explicit dimensions from the current query, if any."""
    group_match = _GROUP_BY_RE.search(query)
    if group_match is not None:
        return _split_dimensions(group_match.group(1))
    by_match = _BY_RE.search(query)
    if by_match is not None:
        return _split_dimensions(by_match.group(1))
    return None


def _query_filters(query: str) -> OrderedDict[str, str]:
    """Extract small deterministic filter fragments from a follow-up."""
    filters: OrderedDict[str, str] = OrderedDict()
    only_match = _ONLY_RE.search(query)
    if only_match is not None:
        filters["species"] = only_match.group(1).lower()
        return filters
    lowered = query.lower()
    if _dataset_from_query(lowered) is not None:
        for_match = _FOR_RE.search(query)
        if for_match is not None:
            filters["species"] = for_match.group(1).lower()
    return filters


def _entity_label(prefix: str, value: str) -> str:
    """Render one namespaced semantic label."""
    return f"{prefix}:{value}"


def _entity_id(kind: str, value: str) -> str:
    """Return one deterministic bounded entity id for data context."""
    normalized = _normalize_token(value)[:96]
    return f"{_DATA_NAMESPACE}:{kind}:{normalized}"[:128]


def _task_entity(prefix: str, value: str) -> ContextEntity:
    """Build one ``task`` entity for non-dataset/tabular semantics."""
    label = _entity_label(prefix, value)
    return ContextEntity(
        entity_id=_entity_id(prefix, value),
        entity_type="task",
        label=label,
    )


def _data_entities(intent: _StructuredIntent) -> list[ContextEntity]:
    """Project one structured intent into bounded context entities."""
    entities: list[ContextEntity] = []
    for dataset_id in intent.dataset_ids:
        entities.append(
            ContextEntity(
                entity_id=_entity_id("dataset", dataset_id),
                entity_type="dataset",
                label=dataset_id,
            )
        )
    for table_id in intent.table_ids:
        entities.append(
            ContextEntity(
                entity_id=_entity_id("table", table_id),
                entity_type="table",
                label=table_id,
            )
        )
    for dimension in intent.dimensions:
        entities.append(_task_entity("dimension", dimension))
    for filter_value in intent.filters:
        entities.append(_task_entity("filter", filter_value))
    for column in intent.columns:
        entities.append(_task_entity("column", column))
    if intent.row_count is not None:
        entities.append(_task_entity("row_count", str(intent.row_count)))
    return entities


def _intent_from_projection(
    projection: ContextProjection,
) -> _StructuredIntent:
    """Recover the prior Bot-owned Data intent from bounded context."""
    dataset_ids: list[str] = []
    table_ids: list[str] = []
    dimensions: list[str] = []
    filters: list[str] = []
    columns: list[str] = []
    row_count: int | None = None
    for entity in projection.active_entities:
        if entity.entity_id.startswith(f"{_DATA_NAMESPACE}:dataset:"):
            dataset_ids.append(entity.label)
            continue
        if entity.entity_id.startswith(f"{_DATA_NAMESPACE}:table:"):
            table_ids.append(entity.label)
            continue
        if not entity.entity_id.startswith(f"{_DATA_NAMESPACE}:"):
            continue
        if entity.label.startswith("dimension:"):
            dimensions.append(entity.label.split(":", 1)[1])
        elif entity.label.startswith("filter:"):
            filters.append(entity.label.split(":", 1)[1])
        elif entity.label.startswith("column:"):
            columns.append(entity.label.split(":", 1)[1])
        elif entity.label.startswith("row_count:"):
            try:
                row_count = int(entity.label.split(":", 1)[1])
            except ValueError:
                row_count = None
    artifact_id = (
        projection.artifact_refs[-1].artifact_id
        if projection.artifact_refs
        else None
    )
    summary = _validated_aggregate_summary(projection.task_summary)
    try:
        return _StructuredIntent(
            dataset_ids=_normalize_list(dataset_ids),
            table_ids=_normalize_list(table_ids),
            dimensions=_normalize_list(dimensions),
            filters=_normalize_list(filters),
            columns=_normalize_list(columns, limit=_MAX_INTENT_COLUMNS),
            row_count=row_count,
            aggregate_summary=summary,
            artifact_id=_validated_text(artifact_id, limit=128),
        )
    except ValidationError:
        return _StructuredIntent()


def _intent_with_query(
    previous: _StructuredIntent,
    *,
    query: str,
) -> _StructuredIntent:
    """Merge one current query into the prior structured intent."""
    dataset_ids = list(previous.dataset_ids)
    if (dataset := _dataset_from_query(query)) is not None:
        dataset_ids = [dataset]
    explicit_dimensions = _query_dimensions(query)
    dimensions = (
        list(explicit_dimensions)
        if explicit_dimensions is not None and explicit_dimensions
        else list(previous.dimensions)
    )
    filters_map: OrderedDict[str, str] = OrderedDict()
    for filter_value in previous.filters:
        key, _sep, value = filter_value.partition("=")
        if key and value:
            filters_map[key] = value
    for key, value in _query_filters(query).items():
        filters_map[key] = value
    return _StructuredIntent(
        dataset_ids=_normalize_list(dataset_ids),
        table_ids=previous.table_ids,
        dimensions=_normalize_list(dimensions),
        filters=_normalize_list(
            [f"{key}={value}" for key, value in filters_map.items()]
        ),
        columns=previous.columns,
        row_count=previous.row_count,
        aggregate_summary=previous.aggregate_summary,
        artifact_id=previous.artifact_id,
    )


def _standalone_query(intent: _StructuredIntent, current_query: str) -> str:
    """Render a deterministic standalone query from the merged intent."""
    dataset = intent.dataset_ids[0] if intent.dataset_ids else None
    if dataset is None:
        return _SPACE_RE.sub(" ", current_query).strip()
    parts = [f"Show {dataset}"]
    if intent.dimensions:
        parts.append("by " + ", ".join(intent.dimensions))
    filters = OrderedDict()
    for item in intent.filters:
        key, _sep, value = item.partition("=")
        if key and value:
            filters[key] = value
    if filters:
        if list(filters.keys()) == ["species"]:
            parts.append(f"for {filters['species']}")
        else:
            parts.append(
                "where "
                + " and ".join(
                    f"{key} = {value}" for key, value in filters.items()
                )
            )
    return " ".join(parts)


def _raw_payload(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the DataAgent raw payload from a native run envelope."""
    payload = result.get("result")
    if isinstance(payload, Mapping):
        raw = payload.get("raw")
        if isinstance(raw, Mapping):
            return raw
    return result


def _formatted_payload(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the DataAgent formatted payload from a native run envelope."""
    payload = result.get("result")
    if isinstance(payload, Mapping):
        formatted = payload.get("formatted")
        if isinstance(formatted, Mapping):
            return formatted
    return {}


def _mapping_strings(
    mapping: Mapping[str, Any],
    *keys: str,
    limit: int = _MAX_INTENT_ITEMS,
) -> tuple[str, ...]:
    """Collect ordered strings from one or more candidate keys."""
    values: list[str] = []
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            if any(not isinstance(item, str) for item in value):
                return ()
            values.extend(value)
    validated = _validated_list(values, limit=limit)
    return validated or ()


def _headers(result: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the bounded column list from raw or formatted payloads."""
    raw = _raw_payload(result)
    header = raw.get("header")
    if isinstance(header, Sequence) and not isinstance(header, str):
        captions = []
        for item in header:
            if isinstance(item, Mapping):
                caption = item.get("caption") or item.get("name")
                if isinstance(caption, str):
                    captions.append(caption)
                else:
                    return ()
            else:
                return ()
        normalized = _validated_list(captions, limit=_MAX_INTENT_COLUMNS)
        if normalized:
            return normalized
    formatted = _formatted_payload(result)
    tabular = formatted.get("tabular")
    if isinstance(tabular, Mapping):
        headers = tabular.get("headers")
        if isinstance(headers, Sequence) and not isinstance(headers, str):
            if any(not isinstance(item, str) for item in headers):
                return ()
            normalized = _validated_list(
                headers,
                limit=_MAX_INTENT_COLUMNS,
            )
            return normalized or ()
    return ()


def _row_count(result: Mapping[str, Any]) -> int | None:
    """Return the bounded row count from raw or formatted payloads."""
    raw = _raw_payload(result)
    rows = raw.get("data")
    if isinstance(rows, Sequence) and not isinstance(rows, str):
        return len(list(rows))
    formatted = _formatted_payload(result)
    tabular = formatted.get("tabular")
    if isinstance(tabular, Mapping):
        rows = tabular.get("rows")
        if isinstance(rows, Sequence) and not isinstance(rows, str):
            return len(list(rows))
    return None


def _summary(result: Mapping[str, Any]) -> str | None:
    """Return a bounded aggregate summary without raw rows or SQL."""
    raw = _raw_payload(result)
    return _validated_aggregate_summary(raw.get("aggregate_summary"))


def _artifact_upserts(result: Mapping[str, Any]) -> list[ArtifactRefV1]:
    """Project a bounded artifact reference when one is present."""
    raw = _raw_payload(result)
    artifact_id = _validated_text(
        raw.get("artifact_id")
        or raw.get("artifact")
        or raw.get("artifact_ref"),
        limit=128,
    )
    if artifact_id is None:
        return []
    try:
        return [
            ArtifactRefV1(
                artifact_id=artifact_id,
                display_name="data-result",
            )
        ]
    except ValidationError:
        return []


def _result_metadata(result: Mapping[str, Any]) -> _DataResultMetadata:
    """Reduce a successful Data result to bounded semantic metadata."""
    raw = _raw_payload(result)
    return _DataResultMetadata(
        dataset_ids=_mapping_strings(raw, "dataset_id", "dataset_ids"),
        table_ids=_mapping_strings(raw, "table_id", "table_ids"),
        columns=_headers(result),
        row_count=_row_count(result),
        aggregate_summary=_summary(result),
        artifact_upserts=tuple(_artifact_upserts(result)),
    )


class DataConversationAdapter:
    """Prepare Data V1 turns without persisting raw rows or SQL."""

    def __init__(self) -> None:
        self._prepared: _PreparedDataTurn | None = None
        self._captured_result: _DataResultMetadata | None = None

    def prepare(self, projection: ContextProjection) -> dict[str, str]:
        """Merge the current follow-up into the prior bounded data intent."""
        query = _SPACE_RE.sub(" ", projection.current_query).strip()
        previous = _intent_from_projection(projection)
        merged = _intent_with_query(previous, query=query)
        prepared = _PreparedDataTurn(
            standalone_query=_standalone_query(merged, query),
            dialog_id=f"{projection.agent_thread_id}-nl2sql",
            thread_id=projection.agent_thread_id,
            prior_entity_ids=tuple(
                entity.entity_id
                for entity in projection.active_entities
                if entity.entity_id.startswith(f"{_DATA_NAMESPACE}:")
            ),
            intent=merged,
        )
        self._prepared = prepared
        return {
            "user_query": prepared.standalone_query,
            "rewrite_query": prepared.standalone_query,
            "dialog_id": prepared.dialog_id,
            "thread_id": prepared.thread_id,
        }

    def capture_result(self, result: Mapping[str, Any]) -> None:
        """Retain only bounded metadata from the private raw handler result."""
        self._captured_result = _result_metadata(result)

    def delta(self, result: Mapping[str, Any]) -> ContextDelta:
        """Convert one successful Data answer into bounded semantic deltas."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before delta")
        intent = self._prepared.intent
        metadata = self._captured_result or _result_metadata(result)
        artifact_upserts = list(metadata.artifact_upserts)
        merged_intent = _StructuredIntent(
            dataset_ids=metadata.dataset_ids or intent.dataset_ids,
            table_ids=metadata.table_ids or intent.table_ids,
            dimensions=intent.dimensions,
            filters=intent.filters,
            columns=metadata.columns or intent.columns,
            row_count=(
                metadata.row_count
                if metadata.row_count is not None
                else intent.row_count
            ),
            aggregate_summary=(
                metadata.aggregate_summary or intent.aggregate_summary
            ),
            artifact_id=(
                artifact_upserts[0].artifact_id
                if artifact_upserts
                else intent.artifact_id
            ),
        )
        next_entities = _data_entities(merged_intent)
        next_entity_ids = {entity.entity_id for entity in next_entities}
        removals = [
            entity_id
            for entity_id in self._prepared.prior_entity_ids
            if entity_id not in next_entity_ids
        ]
        return ContextDelta(
            summary_update=merged_intent.aggregate_summary,
            entity_upserts=next_entities,
            entity_removals=removals,
            artifact_upserts=artifact_upserts,
        )


__all__ = ["DataConversationAdapter"]
