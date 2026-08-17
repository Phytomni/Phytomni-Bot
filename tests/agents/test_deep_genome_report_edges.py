# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for DeepGenome report synthesis and finalization."""

# pylint: disable=protected-access

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeState
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    DeepGenomeWorkflowError,
)
from mcp_server_phytomni.agents.deep_genome.report import (
    DeepGenomeReportMixin,
    _assemble_final_report,
    _assemble_sections,
    _section_has_data,
    _write_async,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
from mcp_server_phytomni.runtime.deep_genome_transitions import (
    DeepGenomeTransitionError,
)
from mcp_server_phytomni.runtime.locale import SupportedLocale
from tests.agents.shared.deep_genome_fixtures import (
    concrete_barrier_work_items,
    seed_brief_gene_plan,
)
from tests.support.sqlite import closed_sqlite_connection

pytestmark = pytest.mark.unit


class _ReportEdges(DeepGenomeReportMixin):
    """Mixin host exposing report nodes and dispatch seams."""

    def __init__(self) -> None:
        """Wire config plus canned chat and knowledge responses."""
        self.deep_genome_config = DeepGenomeConfig()
        self.chat_calls: list[str] = []
        self.knowledge_calls: list[str] = []
        self.chat_payload: dict[str, Any] = {
            "choices": [{"message": {"content": '["Q1?"]'}}]
        }

    async def _dispatch_chat(
        self,
        user_query: str,
        locale: SupportedLocale | None = None,
    ) -> dict[str, Any]:
        """Record the chat prompt and return the canned payload."""
        del locale
        self.chat_calls.append(user_query)
        return self.chat_payload

    async def _dispatch_knowledge_retrieve(
        self,
        user_query: str,
        repo_id_dict: dict[str, int],
        locale: SupportedLocale | None = None,
    ) -> dict[str, Any]:
        """Record one protocol retrieve and return canned steps."""
        del repo_id_dict, locale
        self.knowledge_calls.append(user_query)
        return {
            "choices": [{"message": {"content": f"steps for {user_query}"}}]
        }


def _state(**overrides: Any) -> DeepGenomeState:
    """Build a DeepGenomeState-shaped mapping with overridable keys."""
    base: dict[str, Any] = {
        "gene_id": "Os01g0177400",
        "species_code": "osa",
        "gene_annotation": {"gene_string": "Os01g0177400 (display)"},
        "preamble": "# Deep Genome Analysis\n\nprofile",
        "synthesize_report": "## Bioinformatic Analysis\n\nbody",
        "discussion_report": "disc",
        "experiment_report": "exp",
        "protocol_report": "proto",
        "part12_combined": "combined",
        "summary_report": "summary",
        "config_params": {"use_analyst_agent": True},
    }
    base.update(overrides)
    return cast(DeepGenomeState, base)


def _usable_barrier_data() -> dict[str, dict[str, str]]:
    """Return two terminal successful concrete rows."""
    return {
        "task_0:evolution_analysis": {
            "analysis_type": "evolution_analysis",
            "status": "success",
        },
        "task_10": {
            "analysis_type": "digital_design",
            "status": "success",
        },
    }


async def test_write_async_reraises_worker_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker OSError is surfaced after the thread finishes."""

    def _boom(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(report_module, "_write", _boom)
    with pytest.raises(OSError, match="disk full"):
        await _write_async(tmp_path / "report.md", "body")


def test_section_has_data_skips_empty_and_none_results() -> None:
    """Missing, empty, and placeholder values keep a section hidden."""
    assert not _section_has_data({}, ("single_cell_summary",))
    assert not _section_has_data(
        {"single_cell_summary": ""}, ("single_cell_summary",)
    )
    assert not _section_has_data(
        {"single_cell_summary": "None Results"},
        ("single_cell_summary",),
    )
    assert _section_has_data(
        {"single_cell_summary": "cells"}, ("single_cell_summary",)
    )


def test_assemble_sections_rewrites_figure_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendered sections keep sequential numbers and rewritten legends."""
    captured: list[dict[str, Any]] = []

    def _prompt(_file: Any, key: str, data: dict[str, Any]) -> str:
        captured.append({"key": key, **data})
        return f"{data['section_number']}:{data.get('tissue_legend', '')}"

    monkeypatch.setattr(report_module, "get_prompt", _prompt)
    body = _assemble_sections(
        {
            "tissue_path": "t.png",
            "tissue_summary": "expr",
            "tissue_legend": "Figure 9 tissue",
            "single_cell_summary": "cells",
        },
        "prompts.yaml",
    )
    assert "1:Figure 1 tissue" in body
    assert any(item["section_number"] == 2 for item in captured)


def test_assemble_final_report_drops_experiment_without_analyst() -> None:
    """Analyst-off layout omits the recommended-experiment block."""
    report = _assemble_final_report(
        _state(config_params={"use_analyst_agent": False})
    )
    assert "## Discussion" in report
    assert "## Recommended experiments" not in report


async def test_public_dispatch_wrappers_reach_private_seams() -> None:
    """Public report seams forward to the private chat and knowledge paths."""
    host = _ReportEdges()
    knowledge = await host.dispatch_knowledge_retrieve(
        "protocol query", {"repo": 3}, "en-US"
    )
    chat = await host.dispatch_chat("chat query", "zh-CN")
    assert knowledge is not None
    assert "steps for protocol query" in str(knowledge)
    assert chat == host.chat_payload


async def test_synthesizer_skip_mode_returns_completed_flag() -> None:
    """Test-mode synthesis skips report assembly and marks the branch done."""
    result = await _ReportEdges()._run_report_synthesizer(
        _state(skip_synthesize=True)
    )
    assert result == {"experiment_completed_branches": 1}


async def test_synthesizer_writes_assembled_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A usable terminal barrier writes the synthesis markdown file."""

    def _prompt(_file: Any, _key: str, data: dict[str, Any]) -> str:
        return f"section-{data['section_number']}"

    monkeypatch.setattr(report_module, "get_prompt", _prompt)
    monkeypatch.setattr(
        report_module, "resolve_scratch_dir", lambda *_a, **_k: str(tmp_path)
    )
    result = await _ReportEdges()._run_report_synthesizer(
        _state(
            work_items=concrete_barrier_work_items(),
            raw_analyst_data=_usable_barrier_data(),
            analyst_summaries={"single_cell_summary": "cells"},
        )
    )
    written = tmp_path / "Os01g0177400_results.md"
    assert written.exists()
    assert "section-1" in written.read_text(encoding="utf-8")
    assert result["experiment_completed_branches"] == 1
    assert "Bioinformatic Analysis" in result["synthesize_report"]


async def test_experiment_node_waits_and_skips_duplicates() -> None:
    """Missing inputs wait; a triggered flag prevents a second design."""
    host = _ReportEdges()
    waiting = await host._run_report_experiment(
        _state(preamble="", synthesize_report="")
    )
    assert waiting == {}
    skipped = await host._run_report_experiment(_state(report_triggered=True))
    assert skipped == {}


async def test_experiment_node_designs_and_fetches_protocols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready barrier designs experiments and attaches protocols."""
    host = _ReportEdges()
    host.chat_payload = {
        "choices": [{"message": {"content": '["ChIP","qPCR"]'}}]
    }
    monkeypatch.setattr(report_module, "get_prompt", lambda *_a, **_k: "P")
    result = await host._run_report_experiment(_state())
    assert result["report_triggered"] is True
    assert "ChIP" in result["experiment_report"]
    assert "qPCR" in result["experiment_report"]
    assert host.knowledge_calls == ["ChIP", "qPCR"]


async def test_protocol_node_reads_chat_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protocol summary keeps the first chat message when present."""
    host = _ReportEdges()
    host.chat_payload = {
        "choices": [{"message": {"content": "protocol-body"}}]
    }
    monkeypatch.setattr(report_module, "get_prompt", lambda *_a, **_k: "P")
    filled = await host._run_report_protocol(_state())
    assert filled == {"protocol_report": "protocol-body"}

    host.chat_payload = {"choices": []}
    empty = await host._run_report_protocol(_state())
    assert empty == {"protocol_report": ""}


async def test_discussion_node_uses_analyst_and_short_layouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discussion prompts include experiments only on the analyst layout."""
    host = _ReportEdges()
    captured: list[dict[str, Any]] = []

    def _prompt(_file: Any, _key: str, data: dict[str, Any]) -> str:
        captured.append(data)
        return "P"

    monkeypatch.setattr(report_module, "get_prompt", _prompt)
    host.chat_payload = {
        "choices": [{"message": {"content": "discussion-body"}}]
    }
    analyst = await host._run_report_discussion(_state())
    assert analyst == {"discussion_report": "discussion-body"}
    assert "Recommended experiments" in captured[0]["content"]

    host.chat_payload = {}
    short = await host._run_report_discussion(
        _state(config_params={"use_analyst_agent": False})
    )
    assert short == {"discussion_report": ""}
    assert "Recommended experiments" not in captured[1]["content"]


async def test_summary_node_returns_message_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summary generation unwraps the chat completion content."""
    host = _ReportEdges()
    host.chat_payload = {"choices": [{"message": {"content": "conclusion"}}]}
    monkeypatch.setattr(report_module, "get_prompt", lambda *_a, **_k: "P")
    result = await host._run_report_summary(_state())
    assert result == {"summary_report": "conclusion"}


def _finalization_fixture(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, str, str]:
    """Build a reserved run with one succeeded concrete work item."""
    db_path = tmp_path / "tasks.db"
    store = DeepGenomeStore(str(db_path))
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/obs/run",
    )
    seed_brief_gene_plan(store, reservation, "Os01g0177400")
    with closed_sqlite_connection(db_path) as conn:
        conn.execute(
            "UPDATE deep_genome_remote_tasks SET status = 'failed', "
            "failure_reason = 'analysis task failed' "
            "WHERE umbrella_task_id = ? AND work_item_key != ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        )
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="SMEP summary",
    )
    return store, str(db_path), reservation.umbrella_task_id


async def test_follow_up_rejects_missing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reserved-looking task id without a snapshot fails closed."""
    db_path = tmp_path / "empty.db"
    DeepGenomeStore(str(db_path))
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: str(db_path),
    )
    with pytest.raises(DeepGenomeWorkflowError, match="tracking unavailable"):
        await _ReportEdges()._run_follow_up_node(
            _state(task_id="missing-task", report_dir=str(tmp_path))
        )


async def test_follow_up_maps_publication_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CAS conflict during publication becomes a workflow error."""
    _store, db_path, task_id = _finalization_fixture(tmp_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *_a, **_k: "P")

    def _conflict(*_args: Any, **_kwargs: Any) -> None:
        raise DeepGenomeTransitionError("revision conflict")

    monkeypatch.setattr(DeepGenomeStore, "publish_final_report", _conflict)
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    with pytest.raises(DeepGenomeWorkflowError, match="publication failed"):
        await _ReportEdges()._run_follow_up_node(
            _state(task_id=task_id, report_dir=str(report_dir))
        )


def test_fail_finalization_maps_store_errors(tmp_path: Path) -> None:
    """A store transition error becomes a tracking-failed workflow error."""

    class _BoomStore(DeepGenomeStore):
        """Raise a transition error from umbrella failure."""

        def fail_umbrella(self, umbrella_task_id: str, *, reason: str) -> None:
            """Reject the failure write."""
            del umbrella_task_id, reason
            raise DeepGenomeTransitionError("cannot fail")

    with pytest.raises(DeepGenomeWorkflowError, match="tracking failed"):
        _ReportEdges()._fail_finalization(
            _BoomStore(str(tmp_path / "tasks.db")),
            "task-1",
            "no usable analysis result",
        )


def test_fail_finalization_maps_sqlite_errors(tmp_path: Path) -> None:
    """A sqlite error during finalization is also a tracking failure."""

    class _BoomStore(DeepGenomeStore):
        """Raise a sqlite error from umbrella failure."""

        def fail_umbrella(self, umbrella_task_id: str, *, reason: str) -> None:
            """Reject the failure write with a durable sqlite error."""
            del umbrella_task_id, reason
            raise sqlite3.Error("locked")

    with pytest.raises(DeepGenomeWorkflowError, match="tracking failed"):
        _ReportEdges()._fail_finalization(
            _BoomStore(str(tmp_path / "tasks.db")),
            "task-1",
            "final synthesis failed",
        )
