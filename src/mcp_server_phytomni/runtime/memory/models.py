# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Domain models and bounded policy for explicit user memory.

The models in this module are storage-neutral.  They deliberately do not
know about SQLite, LangGraph ``Store``, HTTP, or an agent.  A later store
implementation can persist these values while preserving the invariants here:
memory belongs to one user namespace, content is byte-bounded, timestamps are
timezone-aware, and optimistic-concurrency revisions start at one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

DEFAULT_MEMORY_MAX_ITEMS = 100
DEFAULT_MEMORY_MAX_CONTENT_BYTES = 16 * 1024
DEFAULT_MEMORY_MAX_TOTAL_BYTES = 1024 * 1024
DEFAULT_MEMORY_MAX_RETRIEVAL = 20
DEFAULT_MEMORY_MAX_TAGS = 16
DEFAULT_MEMORY_MAX_TAG_BYTES = 128

_MAX_ID_BYTES = 128
_MAX_USER_ID_BYTES = 256
_MAX_KIND_BYTES = 64
_MAX_CONTENT_BYTES = DEFAULT_MEMORY_MAX_CONTENT_BYTES

MemoryId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=_MAX_ID_BYTES),
]
MemoryUserId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=_MAX_USER_ID_BYTES),
]
MemoryKind = Annotated[
    str,
    StringConstraints(min_length=1, max_length=_MAX_KIND_BYTES),
]
MemoryTag = Annotated[
    str,
    StringConstraints(min_length=1, max_length=DEFAULT_MEMORY_MAX_TAG_BYTES),
]


class MemoryPolicyError(ValueError):
    """Raised when a memory value would exceed an explicit policy bound."""


def _validate_text(
    value: str,
    *,
    label: str,
    max_bytes: int,
    allow_empty: bool = False,
) -> str:
    """Reject control characters and enforce a UTF-8 byte ceiling."""
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be blank")
    if value != value.strip() and label != "content":
        raise ValueError(f"{label} must not have surrounding whitespace")
    if any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{label} must not contain control characters")
    encoded_bytes = len(value.encode("utf-8"))
    if encoded_bytes > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _aware_utc(value: datetime | None, *, label: str) -> datetime | None:
    """Require an aware datetime and normalize it to UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC)


class _MemoryPayload(BaseModel):
    """Shared immutable validation for create and persisted memory payloads."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    user_id: MemoryUserId
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=_MAX_CONTENT_BYTES)
    tags: list[MemoryTag] = Field(default_factory=list)
    expires_at: datetime | None = None

    @field_validator("user_id", mode="after")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        """Keep the namespace opaque but reject unsafe/oversize values."""
        return _validate_text(
            value,
            label="user_id",
            max_bytes=_MAX_USER_ID_BYTES,
        )

    @field_validator("kind", mode="after")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        """Keep kind labels small and free of control characters."""
        return _validate_text(value, label="kind", max_bytes=_MAX_KIND_BYTES)

    @field_validator("content", mode="after")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        """Bound content by encoded bytes, not Python character count."""
        return _validate_text(
            value,
            label="content",
            max_bytes=_MAX_CONTENT_BYTES,
        )

    @field_validator("tags", mode="after")
    @classmethod
    def _normalize_tags(cls, values: list[str]) -> list[str]:
        """Trim tags and remove duplicates deterministically."""
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if any(
                ord(character) < 32 or ord(character) == 127
                for character in value
            ):
                raise ValueError("tag must not contain control characters")
            tag = _validate_text(
                value.strip(),
                label="tag",
                max_bytes=DEFAULT_MEMORY_MAX_TAG_BYTES,
            ).strip()
            if not tag:
                raise ValueError("tag must not be blank")
            if tag not in seen:
                seen.add(tag)
                normalized.append(tag)
        if len(normalized) > DEFAULT_MEMORY_MAX_TAGS:
            raise ValueError(
                f"tags exceed the {DEFAULT_MEMORY_MAX_TAGS}-item limit"
            )
        return normalized

    @field_validator("expires_at", mode="after")
    @classmethod
    def _validate_expiry(cls, value: datetime | None) -> datetime | None:
        """Normalize optional expiry timestamps to aware UTC values."""
        return _aware_utc(value, label="expires_at")

    @property
    def content_bytes(self) -> int:
        """Return the UTF-8 byte length of the content."""
        content = str(self.content)
        return len(content.encode("utf-8"))

    @property
    def tag_bytes(self) -> int:
        """Return the combined UTF-8 byte length of all tags."""
        return sum(len(tag.encode("utf-8")) for tag in self.tags)

    @property
    def size_bytes(self) -> int:
        """Return bounded payload bytes counted by the memory policy."""
        return self.content_bytes + self.tag_bytes


class MemoryWrite(_MemoryPayload):
    """Caller-supplied memory fields for an explicit create operation.

    The store, not the caller, assigns ``id``, timestamps, and ``revision``.
    ``MemoryWrite`` is intentionally separate from :class:`MemoryRecord` so a
    request cannot mint a revision or cross a user namespace boundary.
    """


class MemoryRecord(_MemoryPayload):
    """One persisted memory item in a single user namespace.

    ``revision`` starts at one and is incremented by the store on successful
    updates.  A record may be expired when read during retention cleanup; the
    model therefore validates timestamp ordering but does not compare expiry
    with the current wall clock.
    """

    id: MemoryId
    created_at: datetime
    updated_at: datetime
    revision: int = Field(default=1, ge=1)

    @field_validator("id", mode="after")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Keep the persisted id opaque and bounded."""
        return _validate_text(value, label="id", max_bytes=_MAX_ID_BYTES)

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _validate_timestamps(cls, value: datetime, info: object) -> datetime:
        """Normalize persisted timestamps to aware UTC values."""
        label = getattr(info, "field_name", "timestamp")
        result = _aware_utc(value, label=str(label))
        assert result is not None
        return result

    @model_validator(mode="after")
    def _validate_time_order(self) -> Self:
        """Prevent updates or expiry from predating record creation."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.expires_at is not None and self.expires_at < self.created_at:
            raise ValueError("expires_at must not precede created_at")
        return self


MemoryAuditOperation = Literal["create", "update", "delete"]


class MemoryAuditRecord(BaseModel):
    """Digest-only record of one explicit memory mutation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    audit_id: int = Field(ge=1)
    user_id: MemoryUserId
    actor: str = Field(min_length=1, max_length=_MAX_USER_ID_BYTES)
    operation: MemoryAuditOperation
    memory_id: MemoryId
    occurred_at: datetime
    request_id: str | None = None
    before_digest: str | None = None
    after_digest: str | None = None
    revision_before: int | None = Field(default=None, ge=1)
    revision_after: int | None = Field(default=None, ge=1)

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _validate_occurred_at(cls, value: datetime) -> datetime:
        """Normalize the audit timestamp to aware UTC."""
        normalized = _aware_utc(value, label="occurred_at")
        assert normalized is not None
        return normalized


class MemoryPolicy(BaseModel):
    """Per-user bounds shared by the future SQLite store and graph reads.

    ``max_items`` and ``max_total_bytes`` apply to one ``user_id`` namespace;
    ``max_content_bytes`` and tag limits apply to each record; and
    ``max_retrieval`` is the largest number of memories a graph may read in one
    request.  The defaults are intentionally small and can be overridden by a
    later configuration adapter without changing the domain types.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int = Field(default=DEFAULT_MEMORY_MAX_ITEMS, ge=1)
    max_content_bytes: int = Field(
        default=DEFAULT_MEMORY_MAX_CONTENT_BYTES, ge=1
    )
    max_total_bytes: int = Field(default=DEFAULT_MEMORY_MAX_TOTAL_BYTES, ge=1)
    max_retrieval: int = Field(default=DEFAULT_MEMORY_MAX_RETRIEVAL, ge=1)
    max_tags: int = Field(default=DEFAULT_MEMORY_MAX_TAGS, ge=0)
    max_tag_bytes: int = Field(default=DEFAULT_MEMORY_MAX_TAG_BYTES, ge=1)

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        """Keep namespace and per-record limits internally coherent."""
        if self.max_content_bytes > self.max_total_bytes:
            raise ValueError(
                "max_content_bytes must not exceed max_total_bytes"
            )
        if self.max_retrieval > self.max_items:
            raise ValueError("max_retrieval must not exceed max_items")
        if self.max_tags > DEFAULT_MEMORY_MAX_TAGS:
            raise ValueError(
                f"max_tags must not exceed {DEFAULT_MEMORY_MAX_TAGS}"
            )
        if self.max_tag_bytes > DEFAULT_MEMORY_MAX_TAG_BYTES:
            raise ValueError(
                f"max_tag_bytes must not exceed {DEFAULT_MEMORY_MAX_TAG_BYTES}"
            )
        return self

    def validate_record(self, record: _MemoryPayload) -> None:
        """Validate one record against this policy's configurable limits."""
        if record.content_bytes > self.max_content_bytes:
            raise MemoryPolicyError(
                f"content bytes exceed policy limit {self.max_content_bytes}"
            )
        if len(record.tags) > self.max_tags:
            raise MemoryPolicyError(
                f"tags exceed policy limit {self.max_tags}"
            )
        if any(
            len(tag.encode("utf-8")) > self.max_tag_bytes
            for tag in record.tags
        ):
            raise MemoryPolicyError(
                f"tag bytes exceed policy limit {self.max_tag_bytes}"
            )
        if record.size_bytes > self.max_total_bytes:
            raise MemoryPolicyError(
                f"record bytes exceed policy limit {self.max_total_bytes}"
            )

    def validate_write(self, write: MemoryWrite) -> None:
        """Validate a create/update payload against this policy."""
        self.validate_record(write)

    def ensure_capacity(
        self,
        *,
        item_count: int,
        total_bytes: int,
        incoming_bytes: int,
        replacing: bool = False,
        existing_bytes: int = 0,
    ) -> None:
        """Reject a namespace write that would exceed count or byte limits.

        Args:
            item_count: Current number of records in the namespace.
            total_bytes: Current policy-counted bytes in the namespace.
            incoming_bytes: Bytes of the record being inserted or updated.
            replacing: Whether one existing record is being replaced.
            existing_bytes: Bytes of that record before replacement.
        """
        if min(item_count, total_bytes, incoming_bytes, existing_bytes) < 0:
            raise MemoryPolicyError("capacity values must not be negative")
        if replacing and existing_bytes > total_bytes:
            raise MemoryPolicyError(
                "existing replacement bytes exceed namespace total"
            )
        next_count = item_count if replacing else item_count + 1
        next_total = total_bytes - existing_bytes + incoming_bytes
        if next_count > self.max_items:
            raise MemoryPolicyError(
                f"item limit {self.max_items} would be exceeded"
            )
        if next_total > self.max_total_bytes:
            raise MemoryPolicyError(
                f"byte limit {self.max_total_bytes} would be exceeded"
            )

    def bounded_retrieval_limit(self, requested: int | None) -> int:
        """Return a positive request limit capped by ``max_retrieval``."""
        if requested is not None and requested <= 0:
            raise MemoryPolicyError("retrieval limit must be positive")
        return (
            self.max_retrieval
            if requested is None
            else min(requested, self.max_retrieval)
        )


DEFAULT_MEMORY_POLICY = MemoryPolicy()

# These aliases make the domain vocabulary convenient for callers while the
# canonical persisted/read shape remains ``MemoryRecord`` and the create shape
# remains ``MemoryWrite``.
MemoryItem = MemoryRecord
MemoryCreate = MemoryWrite

__all__ = [
    "DEFAULT_MEMORY_MAX_CONTENT_BYTES",
    "DEFAULT_MEMORY_MAX_ITEMS",
    "DEFAULT_MEMORY_MAX_RETRIEVAL",
    "DEFAULT_MEMORY_MAX_TAG_BYTES",
    "DEFAULT_MEMORY_MAX_TAGS",
    "DEFAULT_MEMORY_MAX_TOTAL_BYTES",
    "DEFAULT_MEMORY_POLICY",
    "MemoryCreate",
    "MemoryId",
    "MemoryItem",
    "MemoryKind",
    "MemoryPolicy",
    "MemoryPolicyError",
    "MemoryRecord",
    "MemoryAuditOperation",
    "MemoryAuditRecord",
    "MemoryTag",
    "MemoryUserId",
    "MemoryWrite",
]
