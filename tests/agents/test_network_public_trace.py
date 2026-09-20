# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Gene Network publishes only explicit fixed public narrative facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from mcp_server_phytomni.agents.network.public_trace import (
    publish_gene_network_target_validation,
    publish_gene_network_workflow_selection,
)
from mcp_server_phytomni.runtime.execution_event_sink import (
    bind_execution_event_sink,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    PublicTextPayload,
)


@dataclass
class _RecordingSink:
    """Collect execution-event intents emitted by the public helpers."""

    intents: list[ExecutionEventIntent] = field(default_factory=list)

    def emit(self, intent: ExecutionEventIntent) -> None:
        """Record an execution-event intent for later assertions."""
        self.intents.append(intent)


def test_gene_network_public_narrative_is_fixed_and_domain_validated() -> None:
    """Verify gene network public narrative is fixed and domain validated."""

    recorder = _RecordingSink()
    with bind_execution_event_sink(recorder):
        publish_gene_network_target_validation("TO:0000011", "osa")
        publish_gene_network_workflow_selection("gene_network_analysis")
        with pytest.raises(ValueError, match="invalid trait target"):
            publish_gene_network_target_validation(
                "private prompt https://secret.invalid", "osa"
            )
        with pytest.raises(ValueError, match="unsupported workflow"):
            publish_gene_network_workflow_selection("private_tool_name")

    assert [intent.kind for intent in recorder.intents] == [
        "reasoning.summary",
        "decision.note",
    ]
    assert all(
        isinstance(intent.payload, PublicTextPayload)
        for intent in recorder.intents
    )
    assert [
        cast(PublicTextPayload, intent.payload).text
        for intent in recorder.intents
    ] == [
        "Validated the trait target and species for network analysis.",
        "Selected the declared gene-network analysis workflow.",
    ]
    public = str(
        [intent.model_dump(mode="json") for intent in recorder.intents]
    )
    assert "TO:0000011" not in public
    assert "private" not in public
    assert "secret.invalid" not in public


def test_gene_network_agent_calls_only_the_public_boundary_helpers() -> None:
    """Verify gene network agent calls only the public boundary helpers."""

    source = (
        Path(__file__).parents[2]
        / "src/mcp_server_phytomni/agents/network/agent.py"
    ).read_text(encoding="utf-8")
    assert "publish_gene_network_target_validation(" in source
    assert "publish_gene_network_workflow_selection(" in source
    assert "emit_reasoning_summary(" not in source
    assert "emit_decision_note(" not in source
