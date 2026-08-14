# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-cutting metadata key-set contract test.

One parametrized test per agent with a kitchen-sink
``phytomni_state`` fixture asserting the exact allowed metadata
key set at the formatter boundary. Agents without extra metadata
(Knowledge / Review / BriefGene) are pinned as "no extra metadata"
to lock the stability guarantee.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.support.formatting_fakes import (
    analyst_plan_state,
    design_task_payload,
    network_task_payload,
)

from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
)
from mcp_server_phytomni.mcp.result_formatting import format_tool_result
from mcp_server_phytomni.runtime.run_registry import _terminal_payload

pytestmark = pytest.mark.server


_KITCHEN_SINK_STATE: dict[str, Any] = {
    "user_query": "Show me rice genes on chromosome 1.",
    "rewrite_query": "SELECT gene_id FROM rice WHERE chr = 1;",
    "is_rewrite": True,
    **analyst_plan_state(),
    "goal_description": "Build co-expression network for Os01g0177400",
    "species_code": "osa",
    "retrieve_prompt": "irrelevant intermediate scratch",
    "paper_text": "Long paper text that should not be lifted.",
}


def _data_payload() -> dict[str, Any]:
    """DataAgent kitchen-sink payload."""
    return {
        "header": [{"caption": "gene_id"}],
        "data": [["Os01g01010"]],
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _analyst_payload() -> dict[str, Any]:
    """AnalystAgent kitchen-sink payload."""
    return {
        "task_id": "task-1",
        "output_dir": "/obs/phytomni/run/out",
        "compute_resource": "medium",
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _deep_genome_payload() -> dict[str, Any]:
    """DeepGenomeAgent kitchen-sink submit envelope."""
    return {
        "task_id": "dg-task-1",
        "output_dir": "/obs/phytomni/deep_genome/dg-task-1",
        "compute_resource": "deep-genome",
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _in_silico_payload() -> dict[str, Any]:
    """InSilicoResearchAgent kitchen-sink payload."""
    return {
        "task_ids": {"research_goal_0": "task-abc"},
        "goals": [{"goal": "Identify gene clusters", "context": "rice"}],
        "output_dir": "/obs/phytomni/run/in-silico",
        "error": None,
        "failures": [],
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _digital_design_payload() -> dict[str, Any]:
    """DigitalDesignAgent kitchen-sink payload."""
    payload = design_task_payload(_KITCHEN_SINK_STATE["goal_description"])
    payload["design_task_result"] = payload["design_task_result"][:1]
    payload["task_ids"] = {"protein_design": "prot-1"}
    payload["failures"] = []
    payload["phytomni_state"] = _KITCHEN_SINK_STATE
    return payload


def _gene_network_payload() -> dict[str, Any]:
    """GeneNetworkAgent kitchen-sink payload."""
    payload = network_task_payload(_KITCHEN_SINK_STATE["goal_description"])
    payload["task_ids"] = {"gene_network_analysis": "net-1"}
    payload["failures"] = []
    payload["phytomni_state"] = _KITCHEN_SINK_STATE
    return payload


def _deep_genome_arguments() -> dict[str, Any]:
    """DeepGenomeAgent arguments for formatter routing."""
    return {"species_code": "osa", "gene_id": "Os01g0177400"}


def test_deep_genome_contract_field_order_is_immutable() -> None:
    """The neutral contract owns the public report/progress field order."""
    assert isinstance(DEEP_GENOME_REPORT_FIELDS, tuple)
    assert isinstance(DEEP_GENOME_PROGRESS_FIELDS, tuple)
    assert len(DEEP_GENOME_REPORT_FIELDS) == 10
    assert len(DEEP_GENOME_PROGRESS_FIELDS) == 11
    assert len(set(DEEP_GENOME_REPORT_FIELDS)) == len(
        DEEP_GENOME_REPORT_FIELDS
    )
    assert len(set(DEEP_GENOME_PROGRESS_FIELDS)) == len(
        DEEP_GENOME_PROGRESS_FIELDS
    )


def test_deep_genome_status_uses_only_contract_progress_fields() -> None:
    """MCP status metadata drops private progress keys and preserves order."""
    result = format_tool_result(
        "GetTaskStatus",
        {
            "task_id": "dg-task-1",
            "status": "running",
            "progress": {
                "total": 12,
                "running": 3,
                "submitted_task_id": "must-drop",
            },
        },
    )

    assert tuple(result.metadata["progress"]) == DEEP_GENOME_PROGRESS_FIELDS
    assert result.metadata["progress"]["total"] == 12
    assert result.metadata["progress"]["running"] == 3
    assert "submitted_task_id" not in result.metadata["progress"]


@pytest.mark.parametrize(
    ("agent", "payload", "expected_keys", "arguments"),
    [
        (
            "DataAgent",
            _data_payload(),
            frozenset({"user_query", "rewrite_query", "is_rewrite"}),
            None,
        ),
        (
            "AnalystAgent",
            _analyst_payload(),
            frozenset(
                {
                    "task_id",
                    "output_dir",
                    "compute_resource",
                    "status",
                    "log_status",
                    "plan",
                    "plan_retries",
                    "extracted_tools",
                    "method_context_keys",
                }
            ),
            None,
        ),
        (
            "DeepGenomeAgent",
            _deep_genome_payload(),
            frozenset(
                {
                    "task_id",
                    "output_dir",
                    "species_code",
                    "gene_id",
                    "compute_resource",
                    "status",
                    "log_status",
                }
            ),
            _deep_genome_arguments(),
        ),
        (
            "InSilicoResearchAgent",
            _in_silico_payload(),
            frozenset(
                {
                    "task_id",
                    "task_ids",
                    "output_dir",
                    "goals",
                    "error",
                    "status",
                    "succeeded_count",
                    "failed_count",
                    "failures",
                    "log_status",
                }
            ),
            None,
        ),
        (
            "DigitalDesignAgent",
            _digital_design_payload(),
            frozenset(
                {
                    "task_id",
                    "output_dir",
                    "compute_resource",
                    "status",
                    "log_status",
                    "task_ids",
                    "goal_description",
                    "succeeded_count",
                    "failed_count",
                    "failures",
                }
            ),
            None,
        ),
        (
            "GeneNetworkAgent",
            _gene_network_payload(),
            frozenset(
                {
                    "task_id",
                    "task_ids",
                    "output_dir",
                    "compute_resource",
                    "status",
                    "log_status",
                    "goal_description",
                    "succeeded_count",
                    "failed_count",
                    "failures",
                }
            ),
            None,
        ),
    ],
)
def test_metadata_key_set_is_exact(
    agent: str,
    payload: dict[str, Any],
    expected_keys: frozenset[str],
    arguments: dict[str, Any] | None,
) -> None:
    """Each agent's metadata exposes the exact allowed key set.

    No extras leak from ``phytomni_state`` or ``raw``, and no expected
    keys are missing. The contract is pinned at the formatter boundary
    so a drift in ``result_formatting.py`` or an agent wrapper fails
    fast in CI.
    """
    result = format_tool_result(agent, payload, arguments=arguments)
    assert frozenset(result.metadata.keys()) == expected_keys


@pytest.mark.parametrize(
    ("agent", "state", "expected"),
    [
        (
            "InSilicoResearchAgent",
            {
                "interop": [
                    {
                        "target_id": "peer-a2a",
                        "kind": "a2a",
                        "capability": "research",
                        "status": "completed",
                        "latency_ms": 8.25,
                    }
                ]
            },
            {
                "target_id": "peer-a2a",
                "kind": "a2a",
                "capability": "research",
                "status": "completed",
                "latency_ms": 8.25,
            },
        ),
        (
            "DigitalDesignAgent",
            {
                "interop": [
                    {
                        "target_id": "design-peer",
                        "kind": "mcp",
                        "capability": "design",
                        "status": "degraded",
                        "latency_ms": 0.0,
                    }
                ],
                "degraded_interop": True,
            },
            {
                "target_id": "design-peer",
                "kind": "mcp",
                "capability": "design",
                "status": "degraded",
                "latency_ms": 0.0,
            },
        ),
    ],
)
def test_interop_metadata_projects_safe_delegation_summary(
    agent: str,
    state: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Agent formatters expose only the bounded delegation summary."""
    payload: dict[str, Any]
    if agent == "InSilicoResearchAgent":
        payload = {
            "task_ids": {"research_goal_0": "task-abc"},
            "goals": [],
            "phytomni_state": state,
        }
    else:
        payload = {
            "design_task_result": [],
            "phytomni_state": state,
        }

    metadata = format_tool_result(agent, payload).metadata
    assert metadata["interop"] == [expected]
    if agent == "DigitalDesignAgent":
        assert metadata["degraded_interop"] is True


@pytest.mark.parametrize(
    "agent",
    [
        "KnowledgeAgent",
        "ReviewAgent",
        "BriefGeneAgent",
    ],
)
def test_cited_agents_metadata_all_success(agent: str) -> None:
    """Cited agents surface stability without extra metadata keys.

    All-success runs MUST have empty metadata (back-compat invariant).
    KnowledgeAgent / ReviewAgent / BriefGeneAgent emit plain markdown
    with inline HTML superscript citation markers as ``answer`` and ship
    deduplicated citation documents through ``references``. With no
    ``failures`` recorded on ``phytomni_state`` the formatter returns
    ``metadata == {}`` so clients do not depend on unstable
    intermediate state.
    """
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Evidence [1].",
                    "doc_list": [
                        {"file_id": "doc-a", "title": "Paper A.pdf"},
                    ],
                }
            }
        ],
        "phytomni_state": _KITCHEN_SINK_STATE,
    }
    result = format_tool_result(agent, payload)
    assert result.metadata == {}
    assert "report" not in result.metadata
    assert result.answer == "Evidence <sup>1</sup>."


def test_cited_agents_metadata_degraded_exposes_failures() -> None:
    """ReviewAgent degraded runs expose universal failure keys.

    When ReviewAgent's fan-out workers populate
    ``phytomni_state.failures`` the formatter projects the universal
    failure keys (``status`` / ``succeeded_count`` / ``failed_count`` /
    ``failures``) into ``formatted.metadata``. The other two cited
    agents (KnowledgeAgent / BriefGeneAgent) do not fan out and
    therefore never carry failures — they are not parametrized here.

    ReviewAgent does not populate ``phytomni_state.task_ids`` (its fan-
    out workers do not mint remote task ids), so
    ``project_universal_failure_metadata`` resolves ``succeeded_count``
    to ``0`` on every degraded run; ``status`` therefore lands at
    ``FAILED`` even when only one fan-out call fails. A future change
    that wires per-dim ``task_ids`` writes from review's fan-out
    workers would shift this to ``PARTIAL`` semantics; this test pins
    the current shape so any such change is intentional.
    """
    degraded_state = {
        **_KITCHEN_SINK_STATE,
        "task_ids": {},
        "failures": [
            {
                "task_label": "draft:2",
                "kind": "execute",
                "message": "boom",
                "traceback_digest": "0123456789abcdef",
            }
        ],
    }
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Evidence [1].",
                    "doc_list": [
                        {"file_id": "doc-a", "title": "Paper A.pdf"},
                    ],
                }
            }
        ],
        "phytomni_state": degraded_state,
    }
    result = format_tool_result("ReviewAgent", payload)
    metadata = dict(result.metadata)
    assert set(metadata.keys()) == {
        "status",
        "succeeded_count",
        "failed_count",
        "failures",
    }
    assert metadata["status"] == "FAILED"
    assert metadata["failed_count"] == 1
    assert metadata["succeeded_count"] == 0
    assert metadata["failures"][0]["task_label"].startswith(
        ("draft:", "review_results:", "revised:", "retrieve:", "add_query:")
    )
    assert "traceback_digest" not in metadata["failures"][0]
    assert result.answer == "Evidence <sup>1</sup>."


def test_brief_gene_metadata_does_not_project_literature_degradation() -> None:
    """Internal BriefGene literature degradation stays out of metadata."""
    degraded_state = {
        "final_response": {
            "choices": [
                {"message": {"role": "assistant", "content": "answer"}}
            ]
        },
        "literature_degraded": [
            {"task_label": "OsCAB1", "message": "boom"},
        ],
    }
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
        "phytomni_state": degraded_state,
    }
    result = format_tool_result("BriefGeneAgent", payload)
    metadata = dict(result.metadata)
    assert not metadata


def test_terminal_payload_contract_analyst_class() -> None:
    """analyst-class terminal payload pins formatted.answer + artifact paths.

    The carve-out is implemented in ``run_registry._terminal_payload``;
    this locks it at the file the plan names as its contract home. A
    succeeded analyst-class run with no child ``final_report`` ships the
    structured blocks plus a ``formatted.answer`` and per-task ``paths``.
    """
    rows = [
        {
            "task_id": "net-7",
            "status": "succeeded",
            "output_dir": "/obs/phytomni/net7",
            "final_report": None,
        }
    ]
    products = [
        {
            "task_id": "net-7",
            "output_dir": "/obs/phytomni/net7",
            "paths": ["/obs/phytomni/net7/network.png"],
        }
    ]

    payload, error = _terminal_payload(
        "succeeded", rows, products, "Analysis complete."
    )

    assert error is None
    assert payload is not None
    assert {
        "task_results",
        "live_status",
        "artifacts",
        "final_report",
        "formatted",
    } <= set(payload)
    assert payload["formatted"]["answer"] == "Analysis complete."
    assert payload["artifacts"][0]["paths"] == [
        "/obs/phytomni/net7/network.png"
    ]
    assert payload["final_report"] is None


def test_terminal_payload_contract_deep_genome() -> None:
    """deep_genome terminal payload surfaces both final_report and answer.

    formatted.answer is always present when the caller provides one,
    so deep_genome exposes the compact display text alongside the
    long-form report markdown.
    """
    rows = [
        {
            "task_id": "dg-7",
            "status": "succeeded",
            "output_dir": "/obs/phytomni/dg7",
            "final_report": "# Deep Genome Analysis",
        }
    ]

    payload, _ = _terminal_payload("succeeded", rows, [], "compact answer")

    assert payload is not None
    assert payload["final_report"] == "# Deep Genome Analysis"
    assert payload["formatted"]["answer"] == "compact answer"
