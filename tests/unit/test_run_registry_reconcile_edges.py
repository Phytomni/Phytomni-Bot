# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Extra reconcile edges kept out of the main module line cap."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_run_registry import _make_registry, _seed_async_run
from tests.unit.test_run_registry_reconcile import (
    _empty_lister,
    _FakeReportResult,
    _final_report_assembly,
)

from mcp_server_phytomni.runtime import run_registry, run_registry_reports
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.run_registry import RunSpec
from mcp_server_phytomni.runtime.terminal_report import TerminalReportAssembly
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_reconcile_non_target_agent_skips_terminal_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-analyst-class agents never invoke terminal report synthesis."""
    registry, manager, _unused = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-chat", "alice", "chat", "remote"),
        ("task-1",),
    )
    called = {"n": 0}

    async def fake_reconcile_task(task_id: str) -> dict[str, Any]:
        """Return a succeeded task row."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    async def tracking_synthesize(_context: Any) -> Any:
        """Track that synthesis was invoked (should not happen here)."""
        called["n"] += 1
        return _FakeReportResult()

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile_task)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        tracking_synthesize,
    )

    record = await registry.reconcile(
        "run-chat", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert called["n"] == 0
    assert record.result is not None
    assert record.result["final_report"] is None


_VALID_CHILD_DIR = "/obs/bucket/out/part-001"
_INVALID_CHILD_DIR = "/obs/bucket/out/part-002"
_HANDOFF_LISTING = (
    "data/result.dat",
    "inventory.json",
    "nested.zip",
    ".phytomni-artifacts.json",
)
_MIXED_CHILD_DIRS = {
    "child-001": _VALID_CHILD_DIR,
    "child-002": _INVALID_CHILD_DIR,
}


def _mixed_listed(output_dir: str, relative_path: str) -> ListedArtifactObject:
    """Build one listed object with a non-empty download reference."""
    return ListedArtifactObject(
        relative_path=relative_path,
        source_path=f"{output_dir}/{relative_path}",
        size_bytes=64,
        download_ref=f"{output_dir}/{relative_path}",
    )


async def _mixed_object_lister(
    output_dir: str,
) -> list[ListedArtifactObject]:
    """List a valid report child or an UNKNOWN 4019-shaped sibling."""
    if output_dir == _VALID_CHILD_DIR:
        return [_mixed_listed(output_dir, "scientific_report.md")]
    if output_dir == _INVALID_CHILD_DIR:
        return [_mixed_listed(output_dir, path) for path in _HANDOFF_LISTING]
    raise AssertionError(f"unexpected output_dir: {output_dir}")


async def _mixed_manifest(output_dir: str) -> dict[str, object]:
    """Return a 1.0 report manifest or a non-contract producer payload."""
    if output_dir == _VALID_CHILD_DIR:
        return {
            "artifacts": [
                {
                    "media_type": "text/markdown",
                    "path": "scientific_report.md",
                    "role": "scientific_report",
                }
            ],
            "version": "1.0",
        }
    if output_dir == _INVALID_CHILD_DIR:
        return {"gene": "AT1G73950", "total_files": 116}
    raise AssertionError(f"unexpected output_dir: {output_dir}")


async def _succeeded_mixed_child(task_id: str) -> dict[str, Any]:
    """Return two succeeded children under one common run root."""
    return {
        "task_id": task_id,
        "status": "succeeded",
        "output_dir": _MIXED_CHILD_DIRS[task_id],
    }


def _seed_mixed_delivery_run(
    registry: Any, manager: Any, agent: str
) -> RunSpec:
    """Insert a delivery-required run with valid and invalid children."""
    spec = RunSpec(f"run-salvage-{agent}", "alice", agent, "remote")
    _seed_async_run(registry, manager, spec, ("child-001", "child-002"))
    assert registry.update_running_result(
        spec.run_id,
        owner="alice",
        result=empty_execution_projection(result_archive_required=True),
    )
    return spec


async def _assemble_mixed_report(
    agent: str, **kwargs: Any
) -> TerminalReportAssembly:
    """Keep science from the valid child; UNKNOWN files stay merged."""
    assert kwargs["context"].agent == agent
    artifacts = tuple(kwargs["artifacts"])
    roles = {artifact.role for artifact in artifacts}
    assert ArtifactRole.SCIENTIFIC_REPORT in roles
    assert ArtifactRole.UNKNOWN in roles
    scientific = [
        artifact
        for artifact in artifacts
        if artifact.role is ArtifactRole.SCIENTIFIC_REPORT
    ]
    assert len(scientific) == 1
    assert scientific[0].relative_path == "scientific_report.md"
    unknown_paths = {
        artifact.relative_path
        for artifact in artifacts
        if artifact.role is ArtifactRole.UNKNOWN
    }
    assert "data/result.dat" in unknown_paths
    return _final_report_assembly(agent)


def _patch_mixed_artifact_delivery(
    monkeypatch: pytest.MonkeyPatch,
    registry: Any,
    captured: dict[str, Any],
    agent: str,
) -> None:
    """Stub reconcile, assemble, persist, and delivery without OBS."""
    real_build = run_registry_reports.build_result_archive_inventory

    def capturing_build(groups: Any) -> Any:
        """Capture the shared inventory while using production salvage."""
        inventory = real_build(groups)
        captured["inventory"] = inventory
        return inventory

    async def fake_persist(inventory: Any) -> str:
        """Skip OBS while recording the inventory settlement persisted."""
        captured["persisted"] = inventory
        return "inventory-ref"

    async def fake_assemble(**kwargs: Any) -> TerminalReportAssembly:
        """Bind the agent under test into the shared assembler contract."""
        return await _assemble_mixed_report(agent, **kwargs)

    monkeypatch.setattr(run_registry, "reconcile_task", _succeeded_mixed_child)
    monkeypatch.setattr(
        run_registry, "assemble_terminal_report", fake_assemble
    )
    monkeypatch.setattr(
        run_registry_reports,
        "build_result_archive_inventory",
        capturing_build,
    )
    monkeypatch.setattr(
        run_registry_reports,
        "_persist_report_inventory",
        fake_persist,
    )
    monkeypatch.setattr(
        registry, "_schedule_delivery", lambda *_args, **_kwargs: None
    )


def _assert_mixed_artifact_delivery(
    record: Any,
    captured: dict[str, Any],
    agent: str,
    manager: Any,
) -> None:
    """Check shared science, salvage members, warning, and delivery."""
    assert record is not None
    assert record.result is not None
    result = record.result
    answer = result["formatted"]["answer"]
    assert answer == _final_report_assembly(agent).answer
    assert answer.strip()
    assert "AT1G73950" not in answer
    assert "total_files" not in answer
    assert "inventory.json" not in answer
    inventory = captured["inventory"]
    assert captured.get("persisted") is inventory
    archive_paths = [member.archive_path for member in inventory.members]
    assert archive_paths == [
        "results/part-001/scientific_report.md",
        "results/part-002/data/result.dat",
    ]
    joined = "\n".join(archive_paths)
    assert "inventory.json" not in joined
    assert "nested.zip" not in joined
    assert ".phytomni-artifacts.json" not in joined
    warning_codes = [
        warning["code"] for warning in result["execution"]["warnings"]
    ]
    assert "artifact_manifest_invalid" in warning_codes
    delivery = result["execution"]["delivery"]
    assert delivery["error_code"] != "no_user_deliverables"
    assert delivery["status"] == "pending"
    assert inventory.members
    assert delivery["inventory_digest"] == inventory.digest
    assert manager.get_task_final_report("child-001") == answer


@pytest.mark.parametrize(
    "agent",
    ("analyst", "network", "research", "design"),
)
@pytest.mark.asyncio
async def test_report_agents_share_mixed_artifact_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
) -> None:
    """All four aliases salvage UNKNOWN siblings without replacing science."""
    registry, manager, _unused = _make_registry(tmp_path)
    spec = _seed_mixed_delivery_run(registry, manager, agent)
    captured: dict[str, Any] = {}
    _patch_mixed_artifact_delivery(monkeypatch, registry, captured, agent)
    record = await registry.reconcile(
        spec.run_id,
        owner="alice",
        object_lister=_mixed_object_lister,
        manifest_loader=_mixed_manifest,
    )
    _assert_mixed_artifact_delivery(record, captured, agent, manager)
