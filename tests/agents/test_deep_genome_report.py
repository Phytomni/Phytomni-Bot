# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for DeepGenome report-synthesis pure helpers.

Pins the prompt-feeding helpers downstream LLM nodes depend on:
_state_gene_string at module scope, and _preamble_and_analysis /
_experiment_prompt / _summary_source_content on DeepGenomeReportMixin.
A test-only subclass exposes the protected helpers under public names
so the assertions stay inside the class hierarchy.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeState
from mcp_server_phytomni.agents.deep_genome.report import (
    DeepGenomeReportMixin,
    _state_gene_string,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.unit


class _ReportProbe(DeepGenomeReportMixin):
    """Test-only mixin host exposing protected helpers via public names.

    The mixin's ``_preamble_and_analysis`` / ``_experiment_prompt`` /
    ``_summary_source_content`` helpers are protected because production
    code only calls them from sibling node methods on the same class.
    Tests exercise them through this subclass so the calls stay inside
    the class hierarchy (no pylint W0212 protected-access escape).
    """

    def __init__(self) -> None:
        """Wire the single config attribute the protected helpers read."""
        self.deep_genome_config = DeepGenomeConfig()

    def preamble_and_analysis(self, state: DeepGenomeState) -> str:
        """Public proxy for ``_preamble_and_analysis``."""
        return self._preamble_and_analysis(state)

    def experiment_prompt(self, state: DeepGenomeState, content: str) -> str:
        """Public proxy for ``_experiment_prompt``."""
        return self._experiment_prompt(state, content)

    def summary_source_content(self, state: DeepGenomeState) -> str:
        """Public proxy for ``_summary_source_content``."""
        return self._summary_source_content(state)

    async def run_experiment(self, state: DeepGenomeState) -> dict[str, Any]:
        """Public proxy for the experiment barrier node."""
        return await self._run_report_experiment(state)


def _state(**overrides: Any) -> DeepGenomeState:
    """Build a DeepGenomeState-shaped mapping with overridable keys."""
    base: dict[str, Any] = {
        "gene_id": "Os01g0177400",
        "species_code": "osa",
        "gene_annotation": {"gene_string": "Os01g0177400 (display)"},
        "preamble": (
            "# Deep Genome Analysis of Os01g0177400\n\n"
            "## Gene Profiles\n\nprofile-body"
        ),
        "synthesize_report": (
            "## Bioinformatic Analysis and Molecular Design\n\nanalysis-body"
        ),
        "discussion_report": "disc",
        "experiment_report": "exp",
        "protocol_report": "proto",
        "part12_combined": "combined",
        "config_params": {"use_analyst_agent": True},
    }
    base.update(overrides)
    return cast(DeepGenomeState, base)


def test_state_gene_string_returns_annotation_value() -> None:
    """The helper returns the nested gene_string when present."""
    assert _state_gene_string(_state()) == "Os01g0177400 (display)"


def test_state_gene_string_defaults_to_empty_on_missing_annotation() -> None:
    """A missing gene_annotation block yields an empty string, not KeyError.

    Pins the .get(..., {}).get(..., "") chain that lets early-stage state
    flow through report nodes before annotation has populated.
    """
    assert _state_gene_string(_state(gene_annotation={})) == ""


def test_preamble_and_analysis_joins_preamble_and_synthesize() -> None:
    """``_preamble_and_analysis`` joins the preamble + analysis bodies."""
    state = _state(preamble="PRE", synthesize_report="ANALYSIS")
    assert _ReportProbe().preamble_and_analysis(state) == "PRE\n\nANALYSIS"


def test_preamble_and_analysis_coerces_none_synthesize_to_empty() -> None:
    """A None synthesize_report still produces a well-formed head.

    The dispatch barrier returns {} when analysis branches have not
    completed, which leaves synthesize_report empty/None. The report
    must keep generating instead of templating a literal "None" string.
    """
    state = _state(preamble="PRE", synthesize_report=None)

    assert _ReportProbe().preamble_and_analysis(state) == "PRE\n\n"


def test_experiment_prompt_threads_state_and_content_to_get_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_experiment_prompt`` forwards gene/species/content into get_prompt.

    Captures the args dict get_prompt sees so a future template rename
    or a dropped key surfaces as an assertion failure rather than a
    silent prompt-shape change.
    """
    captured: dict[str, Any] = {}

    def fake_prompt(_file: str, prompt_path: str, args: dict[str, Any]) -> str:
        captured["path"] = prompt_path
        captured["args"] = args
        return "PROMPT"

    monkeypatch.setattr(report_module, "get_prompt", fake_prompt)

    result = _ReportProbe().experiment_prompt(_state(), content="findings")

    assert result == "PROMPT"
    assert captured["path"] == "user/gene_function_experiment"
    assert captured["args"] == {
        "gene_string": "Os01g0177400 (display)",
        "species_string": "rice (Oryza sativa)",
        "content": "findings",
    }


def test_summary_source_content_uses_analyst_layout_by_default() -> None:
    """When use_analyst_agent stays True, all six sections render in order."""
    assert _ReportProbe().summary_source_content(_state()) == (
        "combined\n\n"
        "## Recommended experiments\n\n"
        "proto\n\nexp\n\n"
        "## Discussion\n\ndisc\n\n"
    )


def test_summary_source_content_skips_analyst_sections_when_disabled() -> None:
    """``use_analyst_agent=False`` uses the short part12-only layout.

    Pins the deep_genome non-analyst pathway: the gene-function report
    still has to include part12 plus discussion, just without the
    analyst-driven introduction / protocol / experiment sections.
    """
    state = _state(config_params={"use_analyst_agent": False})

    assert (
        _ReportProbe().summary_source_content(state)
        == "combined\n\n## Discussion\n\ndisc\n\n"
    )


def test_summary_source_content_defaults_to_analyst_layout_when_unset() -> (
    None
):
    """Missing ``config_params`` is treated as use_analyst_agent=True.

    Belt-and-braces guard for callers that build state without seeding
    config_params yet — the analyst-aware layout is the safer default
    because it includes more sections.
    """
    result = _ReportProbe().summary_source_content(_state(config_params={}))

    assert "## Recommended experiments" in result
    assert "## Discussion" in result


async def test_experiment_barrier_skips_analyst_work_when_disabled() -> None:
    """The analyst-off layout bypasses the two-branch experiment barrier."""
    state = _state(
        config_params={"use_analyst_agent": False},
        experiment_completed_branches=1,
        analysis_tasks=[],
    )

    result = await _ReportProbe().run_experiment(state)

    assert result["part12_combined"] == (
        "# Deep Genome Analysis of Os01g0177400\n\n"
        "## Gene Profiles\n\nprofile-body\n\n"
        "## Bioinformatic Analysis and Molecular Design\n\nanalysis-body"
    )
    assert result["report_triggered"] is True


class _FollowUpProbe(DeepGenomeReportMixin):
    """Report mixin host with a canned chat dispatch + public node proxy.

    The follow-up node calls the shared chat subgraph; the probe stubs
    ``_dispatch_chat`` so the test stays offline and focuses on the
    final-report persistence side effect rather than the LLM round-trip.
    """

    def __init__(self) -> None:
        """Wire the single config attribute the node reads."""
        self.deep_genome_config = DeepGenomeConfig()

    async def _dispatch_chat(self, user_query: str) -> dict[str, Any]:
        """Return a canned follow-up chat response (JSON-list content)."""
        del user_query
        return {"choices": [{"message": {"content": '["Q1?", "Q2?"]'}}]}

    async def run_follow_up_node(
        self, state: DeepGenomeState
    ) -> dict[str, Any]:
        """Public proxy for ``_run_follow_up_node`` (in-hierarchy access)."""
        return await self._run_follow_up_node(state)


def test_run_follow_up_node_persists_assembled_report_to_task_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The follow-up node writes the assembled markdown to the task row.

    DeepGenome runs in the background and the poll path reads the row, so
    the last report node must persist the assembled markdown (the same
    string it writes to disk, including the follow-up section) keyed by
    ``state['task_id']``. A later ``update_task`` status flip leaves the
    report intact (targeted column write).

    Args:
        tmp_path: Pytest temp directory fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the file + persisted-row assertions pass.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    mgr = TaskManager(db_path)
    mgr.record_submission("dg-task-1", "submitted", "/obs/run")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id="dg-task-1",
        report_dir=str(report_dir),
        part12_combined=(
            "# Deep Genome Analysis of Os01g0177400\n\n"
            "## Gene Profiles\n\nprofile\n\n"
            "## Bioinformatic Analysis and Molecular Design\n\nanalysis"
        ),
        summary_report="conclusion",
        follow_up_questions=[],
    )

    out = asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert (report_dir / "Os01g0177400_report.md").exists()
    persisted = mgr.get_task_final_report("dg-task-1")
    assert persisted is not None
    # The preamble title (carried by part12_combined) reaches the report.
    assert "# Deep Genome Analysis of Os01g0177400" in persisted
    assert "## Follow up questions:" in persisted
    assert "Q1?" in persisted
    # The status flip after the background run must not wipe the report.
    mgr.update_task("dg-task-1", "succeeded", "", "/obs/run")
    assert mgr.get_task_final_report("dg-task-1") == persisted
    assert out["follow_up_questions"] == ["Q1?", "Q2?"]


def test_run_follow_up_node_skips_persist_without_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing ``task_id`` must not crash the node's persistence step.

    The persistence write is best-effort: when no umbrella task id is in
    state (defensive guard), the node still writes the report file and
    returns its delta without raising.

    Args:
        tmp_path: Pytest temp directory fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the no-raise + file-written assertions pass.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id=None,
        report_dir=str(report_dir),
        summary_report="conclusion",
        follow_up_questions=[],
    )

    out = asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert (report_dir / "Os01g0177400_report.md").exists()
    assert out["follow_up_questions"] == ["Q1?", "Q2?"]


def test_run_follow_up_node_swallows_persist_db_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry write failure must not crash the workflow's last node.

    The report is already on disk; the row-persist is best-effort, so a
    ``sqlite3.Error`` (WAL / lock) is logged and swallowed and the node
    still returns its delta. Losing only the poll-surfacing of one run
    is preferable to flipping a finished workflow to ``failed``.

    Args:
        tmp_path: Pytest temp directory fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the no-raise + file-written assertions pass.
    """
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )

    class _BoomManager:
        """TaskManager stand-in whose report write always fails."""

        def __init__(self, _db_path: str) -> None:
            """Accept the db path and ignore it."""

        def set_task_final_report(self, *_: Any) -> bool:
            """Raise as a WAL-locked / busy registry would."""
            raise sqlite3.Error("database is locked")

    monkeypatch.setattr(report_module, "TaskManager", _BoomManager)

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id="dg-boom",
        report_dir=str(report_dir),
        summary_report="conclusion",
        follow_up_questions=[],
    )

    out = asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert (report_dir / "Os01g0177400_report.md").exists()
    assert out["follow_up_questions"] == ["Q1?", "Q2?"]


def test_run_follow_up_node_persists_degraded_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failures channel persists a redacted degraded reason on the row.

    When an optional analysis branch degrades, ``failures`` carries a
    record; the final report node must redact its message and write it to
    the umbrella task row so the poll surface can flag ``degraded``. A
    backend URL in the message is scrubbed before persist.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    mgr = TaskManager(db_path)
    mgr.record_submission("dg-deg-1", "submitted", "/obs/run")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id="dg-deg-1",
        report_dir=str(report_dir),
        summary_report="conclusion",
        follow_up_questions=[],
        failures=[
            {
                "task_label": "evolution_analysis",
                "message": "boom at https://bi.internal:9000/q",
                "kind": "execute",
                "traceback_digest": None,
            }
        ],
    )

    asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert mgr.get_task_degraded("dg-deg-1") == "boom at <redacted-url>"


def test_persist_degraded_uses_literature_degraded_when_no_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Literature-only degradation persists a degraded reason, no failure.

    When the brief_gene mount succeeds overall but rolls up per-symbol
    ``literature_degraded`` records (and no ``failures``), the final
    report node must still persist a status-independent degraded reason
    listing the sorted unique symbols.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    mgr = TaskManager(db_path)
    mgr.record_submission("dg-lit-1", "submitted", "/obs/run")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id="dg-lit-1",
        report_dir=str(report_dir),
        summary_report="conclusion",
        follow_up_questions=[],
        failures=[],
        literature_degraded=[
            {"task_label": "OsB", "message": "boom"},
            {"task_label": "OsA", "message": "boom"},
        ],
    )

    asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert (
        mgr.get_task_degraded("dg-lit-1")
        == "literature retrieval degraded for: OsA, OsB"
    )


def test_run_follow_up_node_no_degraded_for_healthy_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with no failures leaves degraded_reason NULL (no false hit)."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    mgr = TaskManager(db_path)
    mgr.record_submission("dg-ok-1", "submitted", "/obs/run")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    state = _state(
        task_id="dg-ok-1",
        report_dir=str(report_dir),
        summary_report="conclusion",
        follow_up_questions=[],
    )

    asyncio.run(_FollowUpProbe().run_follow_up_node(state))

    assert mgr.get_task_degraded("dg-ok-1") is None
