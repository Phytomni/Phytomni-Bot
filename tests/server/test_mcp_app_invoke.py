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
    """DataAgent payloads expose headers/rows on the tabular field."""
    raw = {
        "header": [{"caption": "Gene"}, {"name": "Identity"}],
        "data": [["Os01g0177400", 0.95]],
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


async def test_invoke_analyst_agent_formats_task_submission(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AnalystAgent maps task fields onto FormattedToolResult.metadata."""
    raw = {
        "task_id": "task-1",
        "output_dir": "/obs/phytomni/run/out",
        "compute_resource": "small",
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
    }


async def test_invoke_deep_genome_agent_threads_arguments_to_formatter(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepGenome formatter pulls species/gene from the request arguments.

    Pins the ``arguments=`` keyword threading from invoke_tool_formatted
    into format_tool_result.
    """
    raw = {"task_id": "dg-task-1"}
    _patch_handler(monkeypatch, PhytomniAgents.DEEP_GENOME_AGENT.value, raw)
    arguments = _payload(demo_data_dir, "deep_genome_agent.json")

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.DEEP_GENOME_AGENT.value, arguments
    )

    assert result.answer == "Server task created successfully:dg-task-1"
    assert result.metadata == {
        "server_id": "dg-task-1",
        "species_code": "osa",
        "gene_id": "Os01g0177400",
        "status": "RUNNING",
    }


async def test_invoke_gene_network_agent_unwraps_nested_task(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GeneNetwork extracts the nested ``network_task`` payload."""
    raw = {
        "network_task": {
            "task_id": "net-1",
            "output_dir": "/obs/phytomni/net/out",
            "compute_resource": "medium",
        }
    }
    _patch_handler(monkeypatch, PhytomniAgents.GENE_NETWORK_AGENT.value, raw)

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.GENE_NETWORK_AGENT.value,
        _payload(demo_data_dir, "gene_network_agent.json"),
    )

    assert result.answer == "Task created successfully:net-1"
    assert result.metadata["task_id"] == "net-1"
    assert result.metadata["compute_resource"] == "analyst-agents-medium"


async def test_invoke_digital_design_agent_collects_subtasks(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DigitalDesign serialises every present design subtask id."""
    raw = {
        "protein_design_task": {
            "task_id": "prot-1",
            "output_dir": "/obs/phytomni/prot",
            "compute_resource": "large",
        },
        "promoter_design_task": {
            "task_id": "prom-1",
            "output_dir": "/obs/phytomni/prom",
            "compute_resource": "large",
        },
        "terminator_design_task": None,
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


async def test_invoke_in_silico_research_agent_falls_back_to_json(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tools without a dedicated formatter dump JSON into the answer."""
    raw = {"plan": ["step-1", "step-2"], "summary": "ok"}
    _patch_handler(
        monkeypatch, PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value, raw
    )

    result = await mcp_app.invoke_tool_formatted(
        PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        _payload(demo_data_dir, "in_silico_research_agent.json"),
    )

    assert json.loads(result.answer) == raw
    assert result.metadata == {}
    assert result.references == ()


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
