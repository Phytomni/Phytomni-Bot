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

from mcp_server_phytomni.mcp.result_formatting import format_tool_result

pytestmark = pytest.mark.server


_KITCHEN_SINK_STATE: dict[str, Any] = {
    "user_query": "Show me rice genes on chromosome 1.",
    "rewrite_query": "SELECT gene_id FROM rice WHERE chr = 1;",
    "is_rewrite": True,
    "plan": "1. retrieve data\n2. analyze\n3. report",
    "plan_feedback": None,
    "plan_retries": 1,
    "extracted_tools": ["pyfasta", "pandas"],
    "tool_usages": "pyfasta -i ...",
    "method_context": {
        "upload": {"path": "sop.pdf"},
        "literature": {"hits": []},
    },
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
    return {
        "design_task_result": [
            {
                "task_id": "prot-1",
                "output_dir": "/obs/phytomni/prot",
                "compute_resource": "large",
            },
        ],
        "task_ids": {"protein_design": "prot-1"},
        "failures": [],
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _gene_network_payload() -> dict[str, Any]:
    """GeneNetworkAgent kitchen-sink payload."""
    return {
        "network_task": {
            "task_id": "net-1",
            "output_dir": "/obs/phytomni/net/out",
            "compute_resource": "medium",
        },
        "task_ids": {"gene_network_analysis": "net-1"},
        "failures": [],
        "phytomni_state": _KITCHEN_SINK_STATE,
    }


def _deep_genome_arguments() -> dict[str, Any]:
    """DeepGenomeAgent arguments for formatter routing."""
    return {"species_code": "osa", "gene_id": "Os01g0177400"}


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
    with inline ``[N]`` citation markers as ``answer`` and ship
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
    assert result.answer == "Evidence [1]."


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
    assert result.answer == "Evidence [1]."
