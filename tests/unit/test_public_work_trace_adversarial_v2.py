# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Adversarial private values fail closed at every work-trace boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "source_passage",
        "sql",
        "query",
        "arguments",
        "result",
        "path",
        "url",
        "credential",
        "provider_body",
        "console_output",
        "exception_text",
    ],
)
def test_named_private_fields_are_rejected_by_shared_public_boundary(
    field: str,
) -> None:
    from mcp_server_phytomni.runtime.public_execution_safety import (
        PublicExecutionDataError,
        validate_public_execution_value,
    )

    with pytest.raises(PublicExecutionDataError):
        validate_public_execution_value(
            {field: "private-fragment"}, max_string_chars=512
        )


def test_adapter_and_presenter_discard_private_provider_shapes() -> None:
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        OPERATION_PRESENTER_REGISTRY,
    )
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        FullSnapshotProviderTraceAdapter,
        ProviderTraceRecord,
    )

    forbidden = {
        "prompt": "hidden prompt",
        "source_passage": "private evidence",
        "sql": "SELECT secret FROM private_table",
        "arguments": {"token": "credential"},
        "result": "tool result",
        "path": "C:\\private\\result.txt",
        "url": "https://secret.invalid/body",
        "credential": "Bearer private-token",
        "provider_body": "raw provider body",
        "console_output": "stderr fragment",
        "exception_text": "password=private",
    }

    def normalize(raw, source_identity, index):
        del index
        if not isinstance(raw, dict):
            return None
        return ProviderTraceRecord.model_validate(
            {
                **raw,
                "source_identity": source_identity,
                "record_class": "semantic_phase",
                "semantic_code": "gene_network.prepare_inputs",
                "status": "running",
            }
        )

    adapted = FullSnapshotProviderTraceAdapter(
        adapter_version="analysis-full-v1",
        normalize_record=normalize,
    ).adapt([forbidden])
    assert adapted.observation.records == ()
    serialized = str(adapted.model_dump(mode="json"))
    assert all(
        value not in serialized
        for value in forbidden.values()
        if isinstance(value, str)
    )

    presented = OPERATION_PRESENTER_REGISTRY.present(
        "gene_network.prepare_inputs",
        detail=forbidden,
        target={"kind": "url", "id": "private-provider-task"},
    )
    assert presented.detail == {}
    assert presented.target is None


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "https://secret.invalid/provider-body",
        "C:\\private\\provider.log",
        "Bearer private-token",
        "password=private",
    ],
)
def test_explicit_provider_summary_still_passes_public_safety_gate(
    unsafe_text: str,
) -> None:
    from mcp_server_phytomni.runtime.gene_network_provider_trace_v2 import (
        present_gene_network_provider_record,
    )
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceObservation,
        ProviderTraceRecord,
    )

    record = ProviderTraceRecord(
        source_identity="private-source-id",
        record_class="public_summary",
        semantic_code="gene_network.reasoning_summary",
        status="running",
        summary_kind="reasoning_summary",
        public_text=unsafe_text,
        explicit_public=True,
    )
    observation = ProviderTraceObservation(
        schema_version=1,
        adapter_version="analysis-delta-v1",
        snapshot_complete=False,
        health="healthy",
        records=(record,),
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        WorkUnitRecord,
    )

    unit = cast(
        WorkUnitRecord,
        SimpleNamespace(
            execution_id="execution-private",
            work_unit_id="work-private",
            parent_span_id="root-span",
            attempt=1,
        ),
    )
    assert (
        present_gene_network_provider_record(
            unit,
            observation,
            record,
            analysis_span_id="analysis-span",
        )
        == ()
    )


def test_normalized_record_contract_forbids_arbitrary_private_fields() -> None:
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceRecord,
    )

    with pytest.raises(ValidationError):
        ProviderTraceRecord.model_validate(
            {
                "source_identity": "record-1",
                "record_class": "semantic_tool",
                "semantic_code": "gene_network.infer_network",
                "status": "running",
                "tool_result": "private result",
            }
        )
