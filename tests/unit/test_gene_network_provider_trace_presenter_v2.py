# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Gene Network provider facts cross only the finite public presenter."""

from __future__ import annotations

from tests.support.provider_trace_v2 import (
    provider_trace_observation,
    provider_trace_presenter_arguments,
    provider_trace_unit,
)

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ProgressPublicPayload,
    WorkUnitPublicPayload,
)
from mcp_server_phytomni.runtime.gene_network_provider_trace_v2 import (
    present_gene_network_provider_record,
)
from mcp_server_phytomni.runtime.provider_trace_v2 import ProviderTraceRecord


def test_recognized_provider_tool_becomes_opaque_analysis_child() -> None:
    """Verify recognized provider tool becomes opaque analysis child."""

    record = ProviderTraceRecord(
        source_identity="provider-record-secret",
        record_class="bounded_progress",
        semantic_code="gene_network.infer_network",
        status="running",
        completed=2,
        total=5,
    )
    intents = present_gene_network_provider_record(
        *provider_trace_presenter_arguments(record),
        analysis_span_id="analysis-span-public",
    )

    assert [intent.type.value for intent in intents] == [
        "work_unit.registered",
        "work_unit.progress",
    ]
    assert all(
        intent.parent_span_id == "analysis-span-public" for intent in intents
    )
    assert all(intent.span_id.startswith("trace-span-") for intent in intents)
    assert all(
        intent.work_unit_id is not None
        and intent.work_unit_id.startswith("trace-work-")
        for intent in intents
    )
    registered_payload = intents[0].public_payload
    progress_payload = intents[1].public_payload
    assert isinstance(registered_payload, WorkUnitPublicPayload)
    assert isinstance(progress_payload, ProgressPublicPayload)
    assert registered_payload.operation_key == ("gene_network.infer_network")
    assert progress_payload.phase == "gene_network.infer_network"
    assert progress_payload.completed == 2
    assert progress_payload.total == 5
    assert progress_payload.unit == "genes"

    public = str([intent.model_dump(mode="json") for intent in intents])
    for private_value in (
        "provider-record-secret",
        "private-analysis-work-unit",
        "private-cursor",
    ):
        assert private_value not in public


def test_unknown_or_mismatched_provider_records_stay_private() -> None:
    """Verify unknown or mismatched provider records stay private."""

    for record in (
        ProviderTraceRecord(
            source_identity="unknown",
            record_class="semantic_tool",
            semantic_code="private.raw_tool",
            status="running",
        ),
        ProviderTraceRecord(
            source_identity="mismatch",
            record_class="semantic_phase",
            semantic_code="gene_network.infer_network",
            status="running",
        ),
    ):
        assert (
            present_gene_network_provider_record(
                provider_trace_unit(),
                provider_trace_observation(record),
                record,
                analysis_span_id="analysis-span-public",
            )
            == ()
        )
