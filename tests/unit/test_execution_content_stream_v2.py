# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transient output revision/offset stream contracts."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.execution_content_stream_v2 import (
    ExecutionContentConflictError,
    ExecutionContentStreamV2,
)


def test_content_stream_resumes_by_revision_and_offset() -> None:
    """Verify content stream resumes by revision and offset."""

    stream = ExecutionContentStreamV2(max_frames_per_execution=4)
    first = stream.publish(
        owner="u1",
        execution_id="turn-content",
        output_revision=1,
        offset=5,
        delta="hello",
    )
    second = stream.publish(
        owner="u1",
        execution_id="turn-content",
        output_revision=1,
        offset=11,
        delta=" world",
    )

    assert stream.list_after(
        owner="u1",
        execution_id="turn-content",
        output_revision=1,
        after_offset=5,
    ) == (second,)
    assert first.model_dump(mode="json") == {
        "schema_version": 2,
        "execution_id": "turn-content",
        "output_revision": 1,
        "offset": 5,
        "delta": "hello",
    }


def test_content_stream_rejects_regression_and_is_owner_scoped() -> None:
    """Verify content stream rejects regression and is owner scoped."""

    stream = ExecutionContentStreamV2(max_frames_per_execution=2)
    stream.publish(
        owner="u1",
        execution_id="turn-content",
        output_revision=2,
        offset=4,
        delta="safe",
    )

    with pytest.raises(ExecutionContentConflictError):
        stream.publish(
            owner="u1",
            execution_id="turn-content",
            output_revision=1,
            offset=8,
            delta="stale",
        )
    assert not stream.list_after(
        owner="u2",
        execution_id="turn-content",
        output_revision=0,
        after_offset=0,
    )


def test_publish_next_allocates_unicode_scalar_end_offsets() -> None:
    """Verify publish next allocates unicode scalar end offsets."""

    stream = ExecutionContentStreamV2()
    first = stream.publish_next(
        owner="alice",
        execution_id="turn-1",
        output_revision=1,
        delta="稻",
    )
    second = stream.publish_next(
        owner="alice",
        execution_id="turn-1",
        output_revision=1,
        delta="rice",
    )

    assert (first.offset, second.offset) == (1, 5)
    assert stream.list_after(
        owner="alice",
        execution_id="turn-1",
        output_revision=1,
        after_offset=first.offset,
    ) == (second,)
