# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Remote Agent provider facts use one finite, Agent-scoped presenter."""

from __future__ import annotations

import pytest
from tests.support.provider_trace_v2 import (
    provider_trace_observation,
    provider_trace_presenter_arguments,
    provider_trace_unit,
)

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    WorkUnitPublicPayload,
)
from mcp_server_phytomni.runtime.gene_network_provider_trace_v2 import (
    present_agent_provider_record,
)
from mcp_server_phytomni.runtime.provider_trace_v2 import (
    ProviderTraceRecord,
    ProviderTraceRecordClass,
)


@pytest.mark.parametrize(
    ("agent_slug", "record_class", "semantic_code"),
    [
        ("analyst", "semantic_phase", "analyst.prepare_analysis"),
        ("deep_genome", "semantic_tool", "deep_genome.run_analysis_branches"),
        ("research", "semantic_tool", "research.collect_evidence"),
        ("design", "semantic_phase", "design.consolidate_candidates"),
        ("network", "semantic_phase", "gene_network.prepare_inputs"),
    ],
)
def test_registered_provider_fact_becomes_agent_scoped_public_work(
    agent_slug: str,
    record_class: ProviderTraceRecordClass,
    semantic_code: str,
) -> None:
    """Verify registered provider fact becomes agent scoped public work."""

    record = ProviderTraceRecord(
        source_identity="provider-record-secret",
        record_class=record_class,
        semantic_code=semantic_code,
        status="running",
    )
    intents = present_agent_provider_record(
        agent_slug,
        *provider_trace_presenter_arguments(record),
        analysis_span_id="analysis-span-public",
    )

    assert [intent.type.value for intent in intents] == [
        "work_unit.registered",
        "work_unit.acknowledged",
    ]
    payload = intents[0].public_payload
    assert isinstance(payload, WorkUnitPublicPayload)
    assert payload.operation_key == semantic_code
    public = str([intent.model_dump(mode="json") for intent in intents])
    assert "provider-record-secret" not in public
    assert "private-analysis-work-unit" not in public
    assert "private-cursor" not in public


def test_cross_agent_unknown_and_non_explicit_summaries_fail_closed() -> None:
    """Verify cross agent unknown and non explicit summaries fail closed."""

    cross_agent = ProviderTraceRecord(
        source_identity="cross-agent",
        record_class="semantic_phase",
        semantic_code="design.validate_target",
        status="running",
    )
    assert not present_agent_provider_record(
        "analyst",
        provider_trace_unit(),
        provider_trace_observation(cross_agent),
        cross_agent,
        analysis_span_id="analysis-span-public",
    )

    summary = ProviderTraceRecord(
        source_identity="private-summary",
        record_class="public_summary",
        semantic_code="analyst.workflow_selected",
        status="running",
        summary_kind="decision",
        public_text="Selected a workflow.",
        explicit_public=True,
    )
    assert not present_agent_provider_record(
        "analyst",
        provider_trace_unit(),
        provider_trace_observation(summary),
        summary,
        analysis_span_id="analysis-span-public",
    )
