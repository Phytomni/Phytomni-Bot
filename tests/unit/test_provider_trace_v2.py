# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite private provider-trace normalization contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _record(**updates):
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceRecord,
    )

    payload = {
        "source_identity": "record-1",
        "record_class": "semantic_phase",
        "semantic_code": "gene_network.prepare_inputs",
        "status": "running",
        "occurred_at": "2026-08-23T00:00:00Z",
        "attempt": 1,
    }
    payload.update(updates)
    return ProviderTraceRecord.model_validate(payload)


def test_provider_trace_contract_accepts_only_finite_normalized_records() -> (
    None
):
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceObservation,
    )

    observation = ProviderTraceObservation(
        schema_version=1,
        adapter_version="analysis-full-v1",
        source_revision=4,
        next_cursor="cursor-4",
        snapshot_complete=True,
        health="healthy",
        records=(_record(),),
    )

    assert observation.records[0].semantic_code == (
        "gene_network.prepare_inputs"
    )
    assert observation.records[0].occurred_at == "2026-08-23T00:00:00Z"

    with pytest.raises(ValidationError):
        _record(record_class="raw_console")
    with pytest.raises(ValidationError):
        _record(content="private console output")
    with pytest.raises(ValidationError):
        _record(completed=5, total=4)
    with pytest.raises(ValidationError):
        ProviderTraceObservation(
            schema_version=1,
            adapter_version="analysis-full-v1",
            snapshot_complete=True,
            health="healthy",
            records=tuple(
                _record(source_identity=f"record-{i}") for i in range(257)
            ),
        )


def test_public_summary_requires_explicit_public_marker() -> None:
    with pytest.raises(ValidationError):
        _record(
            record_class="public_summary",
            summary_kind="decision",
            public_text="Selected the declared workflow.",
        )

    record = _record(
        record_class="public_summary",
        semantic_code="gene_network.workflow_selected",
        status="succeeded",
        summary_kind="decision",
        public_text="Selected the declared workflow.",
        explicit_public=True,
    )
    assert record.public_text == "Selected the declared workflow."


def test_adapter_diagnostics_are_bounded_and_never_retain_raw_payloads() -> (
    None
):
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceAdapterResult,
        ProviderTraceDiagnostics,
        ProviderTraceObservation,
        ProviderTraceRejection,
    )

    result = ProviderTraceAdapterResult(
        observation=ProviderTraceObservation(
            schema_version=1,
            adapter_version="analysis-full-v1",
            snapshot_complete=True,
            health="degraded",
        ),
        diagnostics=ProviderTraceDiagnostics(
            rejected_records=1,
            rejections=(
                ProviderTraceRejection(index=3, code="unknown_record"),
            ),
        ),
    )
    serialized = result.model_dump()
    assert serialized["diagnostics"] == {
        "rejected_records": 1,
        "rejections": ({"index": 3, "code": "unknown_record"},),
    }
    assert "payload" not in str(serialized)
    with pytest.raises(ValidationError):
        ProviderTraceRejection(
            index=0,
            code="unknown_record",
            raw="private",  # type: ignore[call-arg]
        )


def test_full_snapshot_adapter_deduplicates_append_repeat_and_rotation() -> (
    None
):
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        FullSnapshotProviderTraceAdapter,
        ProviderTraceCheckpoint,
        ProviderTraceRecord,
    )

    def normalize(raw, source_identity, index):
        del index
        if not isinstance(raw, dict) or raw.get("code") != "prepare":
            return None
        return ProviderTraceRecord(
            source_identity=source_identity,
            record_class="semantic_phase",
            semantic_code="gene_network.prepare_inputs",
            status=raw["status"],
        )

    adapter = FullSnapshotProviderTraceAdapter(
        adapter_version="analysis-full-v1",
        normalize_record=normalize,
    )
    first = adapter.adapt(
        [
            {"id": "provider-1", "code": "prepare", "status": "running"},
            {"id": "provider-2", "code": "prepare", "status": "succeeded"},
        ]
    )
    assert [
        record.source_identity for record in first.observation.records
    ] == [
        "provider:provider-1",
        "provider:provider-2",
    ]

    checkpoint = ProviderTraceCheckpoint.from_result(first)
    appended = adapter.adapt(
        [
            {"id": "provider-1", "code": "prepare", "status": "running"},
            {"id": "provider-2", "code": "prepare", "status": "succeeded"},
            {"id": "provider-3", "code": "prepare", "status": "succeeded"},
        ],
        checkpoint=checkpoint,
    )
    assert [
        record.source_identity for record in appended.observation.records
    ] == ["provider:provider-3"]

    repeated = adapter.adapt(
        [
            {"id": "provider-1", "code": "prepare", "status": "running"},
            {"id": "provider-2", "code": "prepare", "status": "succeeded"},
            {"id": "provider-3", "code": "prepare", "status": "succeeded"},
        ],
        checkpoint=ProviderTraceCheckpoint.from_result(appended),
    )
    assert repeated.observation.records == ()

    rotated = adapter.adapt(
        [
            {"id": "provider-2", "code": "prepare", "status": "succeeded"},
            {"id": "provider-3", "code": "prepare", "status": "succeeded"},
            {"id": "provider-4", "code": "prepare", "status": "succeeded"},
        ],
        checkpoint=ProviderTraceCheckpoint.from_result(appended),
    )
    assert [
        record.source_identity for record in rotated.observation.records
    ] == ["provider:provider-4"]


def test_structured_delta_adapter_preserves_stable_record_identity() -> None:
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        StructuredDeltaProviderTraceAdapter,
    )

    result = StructuredDeltaProviderTraceAdapter(
        adapter_version="analysis-delta-v1"
    ).adapt(
        {
            "schema_version": 1,
            "adapter_version": "analysis-delta-v1",
            "source_revision": 9,
            "next_cursor": "provider-cursor-9",
            "snapshot_complete": False,
            "health": "healthy",
            "records": [
                {
                    "source_identity": "stable-provider-record-9",
                    "record_class": "semantic_tool",
                    "semantic_code": "gene_network.infer_network",
                    "status": "succeeded",
                    "attempt": 1,
                }
            ],
        }
    )

    assert result.observation.next_cursor == "provider-cursor-9"
    assert result.observation.records[0].source_identity == (
        "stable-provider-record-9"
    )


def test_adapter_version_change_resets_private_cursor_and_overlap() -> None:
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        FullSnapshotProviderTraceAdapter,
        ProviderTraceCheckpoint,
        ProviderTraceRecord,
        StructuredDeltaProviderTraceAdapter,
    )

    def normalize(raw, source_identity, index):
        del raw, index
        return ProviderTraceRecord(
            source_identity=source_identity,
            record_class="semantic_phase",
            semantic_code="gene_network.prepare_inputs",
            status="running",
        )

    old = ProviderTraceCheckpoint(
        adapter_version="analysis-full-v1",
        cursor="full:1",
        source_revision=9,
        overlap_identities=("provider:stable-1",),
    )
    full = FullSnapshotProviderTraceAdapter(
        adapter_version="analysis-full-v2",
        normalize_record=normalize,
    ).adapt([{"id": "stable-1"}], checkpoint=old)
    assert len(full.observation.records) == 1
    assert full.observation.source_revision == 1

    delta = StructuredDeltaProviderTraceAdapter(
        adapter_version="analysis-delta-v2"
    ).adapt(
        {
            "schema_version": 1,
            "adapter_version": "analysis-delta-v2",
            "source_revision": 1,
            "next_cursor": "new-cursor",
            "snapshot_complete": False,
            "health": "healthy",
            "records": [
                {
                    "source_identity": "stable-provider-record-9",
                    "record_class": "semantic_tool",
                    "semantic_code": "gene_network.infer_network",
                    "status": "running",
                }
            ],
        },
        checkpoint=ProviderTraceCheckpoint(
            adapter_version="analysis-delta-v1",
            cursor="old-cursor",
            source_revision=9,
            overlap_identities=("stable-provider-record-9",),
        ),
    )
    assert len(delta.observation.records) == 1
