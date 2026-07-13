# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the explicit cross-session memory domain model and policy."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.runtime.memory.models import (
    DEFAULT_MEMORY_POLICY,
    MemoryPolicy,
    MemoryPolicyError,
    MemoryRecord,
    MemoryWrite,
)

pytestmark = pytest.mark.unit


def _record(**overrides: object) -> MemoryRecord:
    """Build a valid record with deterministic timestamps."""
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "id": "mem-1",
        "user_id": "user-1",
        "kind": "preference",
        "content": "prefers concise answers",
        "tags": ["style", "plant-science"],
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(days=30),
        "revision": 1,
    }
    values.update(overrides)
    return MemoryRecord.model_validate(values)


def test_memory_record_normalizes_tags_and_reports_utf8_size() -> None:
    """Tags are trimmed/deduplicated and size counts encoded bytes."""
    record = _record(
        content="叶绿体",
        tags=["  plant  ", "plant", "biology"],
    )

    assert record.tags == ["plant", "biology"]
    assert record.content_bytes == len("叶绿体".encode())
    assert record.size_bytes == record.content_bytes + sum(
        len(tag.encode("utf-8")) for tag in record.tags
    )


def test_memory_write_is_the_create_shape_without_server_fields() -> None:
    """Create payloads do not allow callers to mint ids or revisions."""
    write = MemoryWrite(
        user_id="user-1",
        kind="fact",
        content="Arabidopsis is a model plant.",
    )

    assert write.tags == []
    assert write.expires_at is None
    assert "id" not in write.model_dump()
    with pytest.raises(ValidationError):
        MemoryWrite.model_validate(
            {
                "user_id": "user-1",
                "kind": "fact",
                "content": "x",
                "id": "caller-minted",
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("user_id", ""),
        ("kind", ""),
        ("content", ""),
        ("revision", 0),
    ],
)
def test_memory_record_rejects_missing_identity_or_content(
    field: str, value: object
) -> None:
    """Identity, content, and revision are always bounded and non-empty."""
    with pytest.raises(ValidationError):
        _record(**{field: value})


def test_memory_record_rejects_oversize_utf8_content() -> None:
    """The model enforces a byte ceiling rather than a character ceiling."""
    with pytest.raises(ValidationError, match="content.*bytes"):
        _record(content="界" * 6000)


def test_memory_record_requires_aware_ordered_timestamps() -> None:
    """Naive timestamps and backwards expiry/update values are invalid."""
    now = datetime(2026, 7, 13, 8, 0)
    with pytest.raises(ValidationError, match="timezone"):
        _record(created_at=now, updated_at=now)

    aware = now.replace(tzinfo=UTC)
    with pytest.raises(ValidationError, match="updated_at"):
        _record(created_at=aware, updated_at=aware - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="expires_at"):
        _record(created_at=aware, expires_at=aware - timedelta(seconds=1))


def test_default_policy_validates_record_and_bounds_retrieval() -> None:
    """The default policy is explicit about item, byte, and read limits."""
    record = _record()
    DEFAULT_MEMORY_POLICY.validate_record(record)

    assert DEFAULT_MEMORY_POLICY.max_items > 0
    assert DEFAULT_MEMORY_POLICY.max_content_bytes > 0
    assert DEFAULT_MEMORY_POLICY.max_total_bytes >= (
        DEFAULT_MEMORY_POLICY.max_content_bytes
    )
    assert DEFAULT_MEMORY_POLICY.max_retrieval > 0
    assert (
        DEFAULT_MEMORY_POLICY.bounded_retrieval_limit(None)
        == DEFAULT_MEMORY_POLICY.max_retrieval
    )
    assert (
        DEFAULT_MEMORY_POLICY.bounded_retrieval_limit(
            DEFAULT_MEMORY_POLICY.max_retrieval + 100
        )
        == DEFAULT_MEMORY_POLICY.max_retrieval
    )


def test_policy_rejects_record_and_namespace_capacity_overages() -> None:
    """Policy-specific limits catch records below model-wide hard ceilings."""
    policy = MemoryPolicy(
        max_items=2,
        max_content_bytes=4,
        max_total_bytes=10,
        max_retrieval=1,
        max_tags=1,
        max_tag_bytes=4,
    )

    with pytest.raises(MemoryPolicyError, match="content bytes"):
        policy.validate_record(_record(content="12345", tags=[]))
    with pytest.raises(MemoryPolicyError, match="tags"):
        policy.validate_record(_record(content="x", tags=["one", "two"]))
    with pytest.raises(MemoryPolicyError, match="item limit"):
        policy.ensure_capacity(item_count=2, total_bytes=0, incoming_bytes=1)
    with pytest.raises(MemoryPolicyError, match="byte limit"):
        policy.ensure_capacity(item_count=0, total_bytes=10, incoming_bytes=1)


def test_policy_rejects_invalid_retrieval_requests() -> None:
    """Zero/negative limits cannot silently become broad reads."""
    with pytest.raises(MemoryPolicyError, match="retrieval"):
        DEFAULT_MEMORY_POLICY.bounded_retrieval_limit(0)
    with pytest.raises(MemoryPolicyError, match="retrieval"):
        DEFAULT_MEMORY_POLICY.bounded_retrieval_limit(-1)
