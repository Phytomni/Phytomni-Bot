# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Canonical empty-science results retain independently ready archives."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from mcp_server_phytomni.mcp.formatting.models import (
    ReportExecution,
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.mcp.formatting.redaction import strip_agent_result
from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_delivery import (
    DeliveryRevision,
    claim_delivery_attempt,
    settle_delivery_ready,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    ReportArtifactSources,
    ReportTerminalState,
    _ReportSettlementRequest,
    canonical_terminal_payload,
    settle_report_terminal,
)
from mcp_server_phytomni.runtime.terminal_artifacts import TerminalArtifactSet
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportAssembly,
    TerminalReportContext,
    assemble_terminal_report,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.server


async def _terminal_state(agent: str, scenario: str) -> ReportTerminalState:
    """Assemble synthetic science before exercising archive persistence."""
    artifact = ClassifiedArtifact(
        source_path="fixture://measurements.parquet",
        relative_path="measurements.parquet",
        role=ArtifactRole.SCIENTIFIC_DATA,
        media_type="application/octet-stream",
        size_bytes=32,
        download_ref="fixture:measurements.parquet",
    )
    artifacts: tuple[ClassifiedArtifact, ...] = (artifact,)
    if scenario == "empty_synthesis":
        artifacts += (
            replace(
                artifact,
                source_path="fixture://results.md",
                relative_path="results.md",
                role=ArtifactRole.SCIENTIFIC_REPORT,
                media_type="text/markdown",
                download_ref="fixture:results.md",
            ),
        )
    live = [
        {
            "task_id": "child-fixture",
            "status": "succeeded",
            "output_dir": "/obs/synthetic-bucket/fixture-owner/run-fixture",
        }
    ]
    model_calls: list[str] = []

    async def reader(reference: str) -> str:
        """Read only the explicitly scientific text fixture."""
        assert reference == "fixture://results.md"
        return "The synthetic treatment increased the signal."

    async def summarizer(prompt: str) -> str:
        """Represent unavailable model output without network calls."""
        model_calls.append(prompt)
        return ""

    report = await assemble_terminal_report(
        context=TerminalReportContext(
            agent=agent,
            status="succeeded",
            live=live,
            artifacts=artifacts,
            query="compare synthetic measurements",
            locale="en-US",
        ),
        artifacts=artifacts,
        reader=reader,
        summarizer=summarizer,
    )
    digest = "sha256:" + "a" * 64
    assert len(model_calls) == (0 if scenario == "no_text" else 1)
    assert report.report.source_artifact_count == len(model_calls)
    return ReportTerminalState(
        status="succeeded",
        live=live,
        artifact_set=TerminalArtifactSet(artifacts=artifacts, warnings=()),
        report=report,
        delivery=ResultDelivery(
            1, True, "pending", 1, digest, None, None, False
        ),
    )


def _assert_public_golden(
    agent: str, scenario: str, status: str, public: object
) -> None:
    """Compare real serializer output with the independently stored fixture."""
    golden_path = (
        Path(__file__).parents[1]
        / "fixtures/report-integrity/terminal-report-integrity.json"
    )
    golden_cases = json.loads(golden_path.read_text(encoding="utf-8"))
    expected_case = next(
        case
        for case in golden_cases
        if case["agent"] == agent and case["scenario"] == scenario
    )
    assert expected_case["status"] == status
    assert expected_case["result"] == public


@pytest.mark.parametrize("agent", ["analyst", "research", "network", "design"])
@pytest.mark.parametrize("scenario", ["no_text", "empty_synthesis"])
@pytest.mark.asyncio
async def test_empty_science_preserves_canonical_ready_archive(
    tmp_path: Path, agent: str, scenario: str
) -> None:
    """Real assembly and archive settlement keep report failure separate."""
    state = await _terminal_state(agent, scenario)
    assert state.delivery is not None
    digest = state.delivery.inventory_digest
    payload, error = canonical_terminal_payload(state)
    assert error is None
    payload["delivery_internal"] = {
        "inventory_ref": "fixture:private-inventory",
        "attempts_claimed": 0,
        "last_error_code": None,
    }
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-fixture", "fixture-owner", agent, "remote"),
        outcome=RunOutcome(status="running", result=payload),
    )
    target = DeliveryRevision("run-fixture", "fixture-owner", 1, digest)
    claim = claim_delivery_attempt(registry, target)
    assert claim is not None
    archive = ResultArchiveDescriptor(
        "result_archive",
        f"{agent}-results.zip",
        "application/zip",
        32,
        True,
        False,
        f"result-archive:{digest}",
    )
    assert settle_delivery_ready(registry, target, archive)
    reopened = RunRegistry(registry.db_path)
    ready = reopened.get_run("run-fixture", owner="fixture-owner")
    assert ready is not None and ready.result is not None
    assert ready.status == "succeeded"
    assert claim.summary_markdown == ""
    assert ready.result["formatted"]["answer"] == ""
    assert ready.result["execution"]["report"] == {
        "state": "degraded",
        "degraded": True,
        "source_artifact_count": 0 if scenario == "no_text" else 1,
    }
    assert ready.result["execution"]["warnings"] == [
        asdict(warning) for warning in state.report.warnings
    ]
    assert [warning.code for warning in state.report.warnings] == [
        (
            "report_no_scientific_text"
            if scenario == "no_text"
            else "report_synthesis_failed"
        )
    ]
    assert ready.result["execution"]["artifacts"] == (
        payload["execution"]["artifacts"]
    )
    assert ready.result["execution"]["tasks"] == payload["execution"]["tasks"]
    assert ready.result["execution"]["delivery"]["archive"] == asdict(archive)
    assert ready.result["execution"]["delivery"]["status"] == "ready"
    assert not reopened.begin_delivery_retry(
        "run-fixture", owner="fixture-owner"
    )
    assert reopened.get_run("run-fixture", owner="other-owner") is None
    assert state.delivery is not None
    expected, error = canonical_terminal_payload(
        replace(
            state,
            delivery=replace(state.delivery, status="ready", archive=archive),
        )
    )
    assert error is None
    public = strip_agent_result(ready.result)
    assert public == strip_agent_result(expected)
    assert "delivery_internal" not in public
    assert "fixture:private-inventory" not in json.dumps(public)
    (tmp_path / f"{agent}-{scenario}-ready.json").write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _assert_public_golden(agent, scenario, ready.status, public)


@pytest.mark.asyncio
async def test_truncated_listing_keeps_successful_report_when_archive_fails(
    tmp_path: Path,
) -> None:
    """A bounded listing failure does not discard a successful report."""
    run_id = "run-truncated-listing"
    db_path = str(tmp_path / "runs.sqlite")
    registry = RunRegistry(db_path)
    registry.create_run(
        RunSpec(run_id, "fixture-owner", "analyst", "remote"),
        outcome=RunOutcome(
            status="running",
            result=empty_execution_projection(result_archive_required=True),
        ),
    )
    current = registry.get_run(run_id, owner="fixture-owner")
    assert current is not None

    async def object_lister(_output_dir: str) -> list[ListedArtifactObject]:
        """Return one more object than the terminal listing cap."""
        return [
            ListedArtifactObject(
                relative_path=f"unknown-{index:03d}.txt",
                source_path=f"fixture://unknown-{index:03d}.txt",
                size_bytes=1,
                download_ref=f"fixture:unknown-{index:03d}.txt",
            )
            for index in range(201)
        ]

    async def manifest_loader(_output_dir: str) -> dict[str, object]:
        """Return an empty manifest so truncation is the only warning."""
        return {"version": "1.0", "artifacts": []}

    async def assemble(**_: object) -> TerminalReportAssembly:
        """Return the already successful scientific report body."""
        return TerminalReportAssembly(
            answer="# Preserved report\n\nThe run completed successfully.",
            report=ReportExecution(
                state="final", degraded=False, source_artifact_count=0
            ),
        )

    settled = await settle_report_terminal(
        _ReportSettlementRequest(
            registry=registry,
            current=current,
            status="succeeded",
            live=[
                {
                    "task_id": "child-fixture",
                    "status": "succeeded",
                    "output_dir": "/obs/synthetic-bucket/fixture-owner/run",
                }
            ],
            sources=ReportArtifactSources(
                object_lister=object_lister,
                manifest_loader=manifest_loader,
            ),
            assembler=assemble,
        )
    )

    assert settled is not None
    assert settled.status == "succeeded"
    assert settled.result is not None
    assert (
        settled.result["formatted"]["answer"]
        == "# Preserved report\n\nThe run completed successfully."
    )
    assert settled.result["execution"]["report"] == {
        "state": "final",
        "degraded": False,
        "source_artifact_count": 0,
    }
    assert settled.result["execution"]["delivery"] == {
        "schema_version": 1,
        "required": True,
        "status": "failed",
        "revision": 1,
        "inventory_digest": "",
        "archive": None,
        "error_code": "archive_inventory_limit_exceeded",
        "retryable": False,
    }
    warning_codes = {
        warning["code"] for warning in settled.result["execution"]["warnings"]
    }
    assert "artifact_listing_truncated" in warning_codes
