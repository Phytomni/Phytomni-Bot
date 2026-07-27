# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-surface sentinels for the scientific/execution result split."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.a2ui_projection import format_review_result
from mcp_server_phytomni.api.lifecycle_contract import (
    canonicalize_agent_run_body,
)
from mcp_server_phytomni.api.openai_mapping import to_chat_completion
from mcp_server_phytomni.api.streaming import failed_stream_result
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import (
    build_tool_result_envelope,
    strip_agent_result,
    strip_chat_completion,
)
from mcp_server_phytomni.runtime.deep_genome_store_projection import (
    DeepGenomeSnapshot,
    snapshot_to_canonical_result,
)

pytestmark = pytest.mark.server


def _sentinel_payload() -> dict[str, Any]:
    """Return operational values that must not be scientific."""
    return {
        "answer": "scientific answer",
        "task_id": "task-sentinel",
        "task_ids": ["task-sentinel", "run-sentinel"],
        "output_dir": "/path-sentinel",
        "output_dirs": ["/path-sentinel"],
        "warnings": [{"code": "warning-sentinel", "stage": "submit"}],
        "artifacts": [
            {
                "artifact_id": "vendor-sentinel",
                "role": "scientific_text",
                "size_bytes": 12,
            }
        ],
    }


def _envelope() -> Any:
    """Build the shared sentinel envelope once per test case."""
    return build_tool_result_envelope("AnalystAgent", _sentinel_payload())


def _canonical_result(result: dict[str, Any]) -> dict[str, Any]:
    """Run a result through the public polling/lifecycle projector."""
    body = canonicalize_agent_run_body(
        {
            "id": "run-sentinel",
            "agent": "research",
            "status": "succeeded",
            "task_ids": [],
            "result": result,
        }
    )
    return body["result"]


def _entrypoint_result(entrypoint: str) -> dict[str, Any]:
    """Build one public result for each migration entrypoint."""
    envelope = _envelope()
    format_agent_run_result = getattr(api_app, "_format_agent_run_result")
    if entrypoint == "native":
        _, result = format_agent_run_result(
            envelope,
            resolve_meta={},
            debug=False,
        )
        return result
    if entrypoint in {"expert", "polling"}:
        _, result = format_agent_run_result(
            envelope,
            resolve_meta={},
            debug=False,
        )
        return _canonical_result(result)
    if entrypoint == "resume":
        result = format_review_result(
            {
                "final_response": _sentinel_payload(),
            }
        )
        return strip_agent_result(result)
    raise AssertionError(f"unknown entrypoint: {entrypoint}")


@pytest.mark.parametrize(
    "entrypoint",
    ["native", "expert", "polling", "resume"],
)
def test_operational_sentinels_only_live_in_execution(
    entrypoint: str,
) -> None:
    """Native, Expert, polling, and resume keep one public split."""
    result = _entrypoint_result(entrypoint)
    formatted = json.dumps(
        {
            key: value
            for key, value in result["formatted"].items()
            if key != "output_dirs"
        }
    )
    execution = json.dumps(result["execution"])

    for sentinel in (
        "task-sentinel",
        "run-sentinel",
        "vendor-sentinel",
        "path-sentinel",
    ):
        assert sentinel not in formatted
        assert sentinel in execution
    assert set(result) == {"formatted", "execution"}


def test_terminal_report_metadata_survives_run_projection() -> None:
    """Polling keeps report metadata and public artifact descriptors."""
    report = {
        "state": "final",
        "degraded": False,
        "source_artifact_count": 1,
    }
    result = _canonical_result(
        {
            "formatted": {
                "answer": "# Final report",
                "follow_up_questions": [],
                "references": [],
                "tabular": {},
                "metadata": {"report": report},
            },
            "execution": {
                "tracking": {"degraded": False},
                "warnings": [],
                "tasks": [
                    {
                        "id": "task-final",
                        "accepted": True,
                        "status": "succeeded",
                    }
                ],
                "artifacts": [
                    {
                        "role": "scientific_report",
                        "name": "report.md",
                        "media_type": "text/markdown",
                        "size_bytes": 32,
                        "downloadable": True,
                        "report_context_eligible": True,
                        "download_ref": "/obs/public/report.md",
                    }
                ],
                "output_dirs": ["/obs/public"],
                "report": report,
                "diagnostics": [],
            },
        }
    )

    assert result["formatted"]["metadata"]["report"] == report
    assert result["execution"]["report"] == report
    assert result["execution"]["artifacts"] == [
        {
            "role": "scientific_report",
            "name": "report.md",
            "media_type": "text/markdown",
            "size_bytes": 32,
            "downloadable": True,
            "report_context_eligible": True,
            "download_ref": "/obs/public/report.md",
        }
    ]


def test_deep_genome_snapshot_uses_canonical_split() -> None:
    """DeepGenome reports use the same scientific/execution result shape."""
    snapshot = DeepGenomeSnapshot(
        umbrella_task_id="dg-final",
        status="succeeded",
        degraded=False,
        degraded_reason=None,
        failures=(),
        report_revision=4,
        report_updated_at="2026-07-25T00:00:00Z",
        progress={
            "planning_complete": True,
            "brief_gene_status": "succeeded",
            "total": 12,
            "completed": 12,
        },
        intermediate_report="# Intermediate report\n",
        final_report="# Final report\n",
        report_stage="final",
        report_completeness="complete",
    )
    result = snapshot_to_canonical_result(
        snapshot,
        existing_result={
            "task_results": [
                {"task_id": "dg-final", "output_dir": "/obs/deep-genome"}
            ],
        },
    )

    assert result["formatted"]["answer"] == "# Final report\n"
    report = result["execution"]["report"]
    assert report == {
        "state": "final",
        "degraded": False,
        "source_artifact_count": 0,
    }
    assert result["formatted"]["metadata"]["report"] == report
    assert result["formatted"]["metadata"]["deep_genome"]["revision"] == 4
    assert result["execution"]["output_dirs"] == ["/obs/deep-genome"]
    assert "final_report" not in result
    assert "intermediate_report" not in result
    assert "artifacts" not in result


def test_stream_settlement_keeps_execution_and_strips_raw() -> None:
    """Pre-open stream failures use the same sibling execution block."""
    result = failed_stream_result()
    assert set(result) == {
        "formatted",
        "execution",
        "raw",
        "stream",
        "partial",
    }
    public = strip_agent_result(result)
    assert set(public) == {"formatted", "execution", "stream", "partial"}
    assert public["execution"]["tracking"] == {"degraded": False}


async def test_mcp_dispatch_adds_execution_without_raw_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP clients receive the additive execution sibling in default mode."""
    envelope = _envelope()

    async def invoke(_name: Any, _arguments: dict[str, Any]) -> Any:
        return envelope

    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    monkeypatch.setattr(mcp_app, "invoke_tool_enveloped", invoke)
    response = await mcp_app.dispatch_tool("AnalystAgent", {})
    body = json.loads(response[0].text)
    assert set(body) == {"formatted", "execution"}
    assert "task-sentinel" not in json.dumps(body["formatted"])
    assert "task-sentinel" in json.dumps(body["execution"])


def test_chat_completion_keeps_execution_after_default_redaction() -> None:
    """OpenAI-compatible default output keeps execution but removes raw."""
    envelope = _envelope()
    completion = to_chat_completion(
        asdict(envelope.formatted),
        envelope.raw,
        "phyto-research",
        asdict(envelope.execution),
    )
    public = strip_chat_completion(completion)
    assert public["execution"] == asdict(envelope.execution)
    assert "raw" not in public
