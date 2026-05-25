# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for invoke_tool_formatted across every public MCP tool.

For each tool, loads its demo_data payload (the same fixture clients
ship with), patches the dispatch handler with a synthetic raw payload
that exercises the matching format_tool_result branch, then asserts
the FormattedToolResult shape. Pins the shared invocation seam so a
formatter or schema drift fails fast in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import FormattedToolResult
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.server


def _payload(demo_data_dir: Path, name: str) -> dict[str, Any]:
    """Load a demo payload as parsed JSON."""
    return json.loads(
        (demo_data_dir / "payloads" / name).read_text(encoding="utf-8")
    )


def _patch_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    response: Any,
) -> None:
    """Replace the dispatch handler with a stub returning ``response``."""

    async def _stub(_args: Any) -> Any:
        return response

    monkeypatch.setitem(mcp_app.TOOL_HANDLERS, tool_name, _stub)


async def test_invoke_tool_raw_returns_handler_payload(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw seam validates args then forwards the handler payload.

    Pins the contract for HTTP routes that need the un-formatted
    payload (BriefGene resolver pre-shaping, etc.).
    """
    sentinel = {"raw": "value"}
    _patch_handler(monkeypatch, PhytomniAgents.CHAT_AGENT.value, sentinel)

    result = await mcp_app.invoke_tool_raw(
        PhytomniAgents.CHAT_AGENT.value,
        _payload(demo_data_dir, "chat_agent.json"),
    )

    assert result is sentinel


async def test_invoke_chat_agent_formats_assistant_message(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatAgent payloads flow through _format_message_result."""
    raw = {
        "choices": [
            {
                "message": {
                    "content": "Hello world.",
                    "follow_up_questions": ["What next?"],
                }
            }
        ]
    }
    _patch_handler(monkeypatch, PhytomniAgents.CHAT_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.CHAT_AGENT.value,
        _payload(demo_data_dir, "chat_agent.json"),
    )

    assert isinstance(result, FormattedToolResult)
    assert result.answer == "Hello world."
    assert result.follow_up_questions == ("What next?",)
    assert result.references == ()


@pytest.mark.parametrize(
    ("tool", "payload_name"),
    [
        (PhytomniAgents.KNOWLEDGE_AGENT.value, "knowledge_agent.json"),
        (PhytomniAgents.REVIEW_AGENT.value, "review_agent.json"),
        (PhytomniAgents.BRIEF_GENE_AGENT.value, "brief_gene_agent.json"),
    ],
)
async def test_invoke_cited_agent_normalizes_references(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    payload_name: str,
) -> None:
    """Cited agents rewrite ``[N]`` markers and dedupe doc references."""
    raw = {
        "choices": [
            {
                "message": {
                    "content": "Background [1]. Conclusion [1].",
                    "doc_list": [
                        {"file_id": "doc-a", "title": "Paper A.pdf"},
                    ],
                }
            }
        ]
    }
    _patch_handler(monkeypatch, tool, raw)

    result = await mcp_app.invoke_tool_formatted(
        tool, _payload(demo_data_dir, payload_name)
    )

    assert result.answer == "Background [1]. Conclusion [1]."
    assert result.references == ({"file_id": "doc-a", "title": "Paper A"},)


async def test_invoke_data_agent_serializes_table(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataAgent payloads expose headers/rows on the tabular field.

    The NL2SQL ``phytomni_state`` lifts ``user_query`` / ``rewrite_query``
    / ``is_rewrite`` into ``metadata`` so the rewrite context survives
    default-mode projection.
    """
    raw = {
        "header": [{"caption": "Gene"}, {"name": "Identity"}],
        "data": [["Os01g0177400", 0.95]],
        "phytomni_state": {
            "user_query": "List rice gene Os01g0177400 identity.",
            "rewrite_query": "SELECT * FROM rice WHERE id='Os01g0177400';",
            "is_rewrite": True,
        },
    }
    _patch_handler(monkeypatch, PhytomniAgents.DATA_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.DATA_AGENT.value,
        _payload(demo_data_dir, "data_agent.json"),
    )

    assert result.tabular == {
        "headers": ["Gene", "Identity"],
        "rows": [["Os01g0177400", 0.95]],
    }
    assert result.answer == "1 row x 2 columns"
    assert result.metadata == {
        "user_query": "List rice gene Os01g0177400 identity.",
        "rewrite_query": "SELECT * FROM rice WHERE id='Os01g0177400';",
        "is_rewrite": True,
    }


async def test_invoke_analyst_agent_formats_task_submission(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AnalystAgent maps task fields onto FormattedToolResult.metadata.

    The wrapper return places ``phytomni_state`` alongside the task
    surface keys; the formatter lifts the planning subset (``plan`` /
    ``extracted_tools`` / ``method_context_keys`` / ``plan_retries``)
    into metadata so default-mode clients can read what the analyst
    actually planned.
    """
    raw = {
        "task_id": "task-1",
        "output_dir": "/obs/phytomni/run/out",
        "compute_resource": "small",
        "phytomni_state": {
            "plan": "1. retrieve\n2. analyze\n3. report",
            "plan_retries": 0,
            "extracted_tools": ["pyfasta"],
            "method_context": {"upload": {}},
        },
    }
    _patch_handler(monkeypatch, PhytomniAgents.ANALYST_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.ANALYST_AGENT.value,
        _payload(demo_data_dir, "analyst_agent.json"),
    )

    assert result.answer == "Task created successfully:task-1"
    assert result.metadata == {
        "task_id": "task-1",
        "output_dir": "/obs/phytomni/run/out",
        "compute_resource": "analyst-agents-small",
        "status": "RUNNING",
        "log_status": "sync_running",
        "plan": "1. retrieve\n2. analyze\n3. report",
        "plan_retries": 0,
        "extracted_tools": ("pyfasta",),
        "method_context_keys": ("upload",),
    }


async def test_invoke_deep_genome_agent_threads_arguments_to_formatter(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepGenome formatter pulls species/gene from the request arguments.

    Pins the ``arguments=`` keyword threading from invoke_tool_formatted
    into format_tool_result.
    """
    raw = {
        "task_id": "dg-task-1",
        "output_dir": "/obs/phytomni/deep_genome/dg-task-1",
        "compute_resource": "deep-genome",
    }
    _patch_handler(monkeypatch, PhytomniAgents.DEEP_GENOME_AGENT.value, raw)
    arguments = _payload(demo_data_dir, "deep_genome_agent.json")

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.DEEP_GENOME_AGENT.value, arguments
    )

    assert result.answer == "Task created successfully:dg-task-1"
    assert result.metadata == {
        "task_id": "dg-task-1",
        "output_dir": "/obs/phytomni/deep_genome/dg-task-1",
        "species_code": "osa",
        "gene_id": "Os01g0177400",
        "compute_resource": "deep-genome",
        "status": "RUNNING",
        "log_status": "sync_running",
    }


async def test_invoke_gene_network_agent_unwraps_nested_task(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GeneNetwork extracts the nested ``network_task`` payload.

    The wrapper return places ``network_task`` at the top level and
    pushes the rest of the LangGraph state under ``phytomni_state``.
    The formatter lifts ``goal_description`` from intermediate state
    so default-mode clients can read what the network analysis
    targeted without flipping ``debug=true``.
    """
    raw = {
        "network_task": {
            "task_id": "net-1",
            "output_dir": "/obs/phytomni/net/out",
            "compute_resource": "medium",
        },
        "phytomni_state": {
            "goal_description": "Build co-expression network for Os01g0177400",
        },
    }
    _patch_handler(monkeypatch, PhytomniAgents.GENE_NETWORK_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.GENE_NETWORK_AGENT.value,
        _payload(demo_data_dir, "gene_network_agent.json"),
    )

    assert result.answer == "Task created successfully:net-1"
    assert result.metadata["task_id"] == "net-1"
    assert result.metadata["compute_resource"] == "analyst-agents-medium"
    # B.5b metadata: goal_description from phytomni_state.
    assert result.metadata["goal_description"] == (
        "Build co-expression network for Os01g0177400"
    )


async def test_invoke_digital_design_agent_collects_subtasks(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DigitalDesign extracts task ids from design_task_result list.

    The agent returns ``design_task_result`` as a list of AnalystAgent
    submission dicts (one per design kind: protein / promoter /
    terminator), accumulated via LangGraph's ``operator.add`` reducer.
    The formatter extracts task ids, output dirs, and the B.5 metadata
    fields (``task_ids``, ``goal_description``) from the list items.
    """
    raw = {
        "design_task_result": [
            {
                "task_id": "prot-1",
                "output_dir": "/obs/phytomni/prot",
                "compute_resource": "large",
            },
            {
                "task_id": "prom-1",
                "output_dir": "/obs/phytomni/prom",
                "compute_resource": "large",
            },
        ],
        "phytomni_state": {
            "goal_description": "Design protein and promoter for Os01g0177400",
        },
    }
    _patch_handler(monkeypatch, PhytomniAgents.DIGITAL_DESIGN_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
        _payload(demo_data_dir, "digital_design_agent.json"),
    )

    assert result.answer == "Tasks created successfully: prot-1,prom-1"
    assert result.metadata["task_id"] == "prot-1"
    # DigitalDesign keeps the raw compute_resource string verbatim; the
    # analyst-agents-* normalisation only runs through _format_task_result.
    assert result.metadata["compute_resource"] == "large"
    # The fan-out paths now live on the typed output_dirs tuple; the
    # legacy metadata["output_dir"] keeps the primary path so consumers
    # of single-task shape keep working.
    assert result.output_dirs == (
        "/obs/phytomni/prot",
        "/obs/phytomni/prom",
    )
    assert result.metadata["output_dir"] == "/obs/phytomni/prot"
    # B.5 metadata: task_ids tuple and goal_description from phytomni_state.
    assert result.metadata["task_ids"] == ("prot-1", "prom-1")
    assert result.metadata["goal_description"] == (
        "Design protein and promoter for Os01g0177400"
    )


async def test_invoke_in_silico_research_agent_formats_goals_and_task_ids(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """InSilicoResearch exposes goals and child task ids in metadata.

    The dedicated formatter replaces the old default-branch JSON
    fallback: ``task_ids`` flattens to a tuple of submitted task ids,
    ``goals`` extracts the goal descriptions, and the shared
    ``output_dir`` mirrors the per-task-suite output path so default-
    mode clients can read the fan-out without flipping debug mode.
    """
    raw = {
        "task_ids": {
            "research_goal_0": "task-abc",
            "research_goal_1": "task-def",
        },
        "goals": [
            {"goal": "Identify gene clusters", "context": "rice"},
            {"goal": "Compare ortholog expression", "context": "drought"},
        ],
        "output_dir": "/obs/phytomni/run/in-silico",
        "error": None,
    }
    _patch_handler(
        monkeypatch, PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value, raw
    )

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        _payload(demo_data_dir, "in_silico_research_agent.json"),
    )

    assert "task-abc" in result.answer
    assert "task-def" in result.answer
    assert result.metadata["task_ids"] == ("task-abc", "task-def")
    assert result.metadata["goals"] == (
        "Identify gene clusters",
        "Compare ortholog expression",
    )
    assert result.metadata["output_dir"] == "/obs/phytomni/run/in-silico"
    assert result.metadata["error"] is None


async def test_invoke_get_task_status_emits_artifacts_on_success(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetTaskStatus formatter surfaces a per-task artifacts descriptor.

    Mirrors the run-aggregate artifacts block at the single-task level
    so a polling client gets the same product index regardless of
    whether it polls the run or the task surface.
    """
    raw = {
        "task_id": "task-1",
        "status": "success",
        "output_dir": "/obs/run/out",
    }
    _patch_handler(monkeypatch, PhytomniAgents.GET_TASK_STATUS.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.GET_TASK_STATUS.value,
        _payload(demo_data_dir, "get_task_status.json"),
    )

    assert result.answer == "Task task-1: success"
    assert result.metadata["status"] == "success"
    assert result.metadata["artifacts"] == [
        {"task_id": "task-1", "output_dir": "/obs/run/out", "paths": []},
    ]
