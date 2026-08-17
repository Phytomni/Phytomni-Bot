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
from types import SimpleNamespace
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
    _state_gene_string,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeStore,
    DeepGenomeTransitionError,
)
from mcp_server_phytomni.runtime.locale import SupportedLocale
from tests.agents.shared.deep_genome_fixtures import (
    concrete_barrier_work_items,
    failed_concrete_barrier_data,
    partially_failed_concrete_barrier_data,
    reserve_smep_finalization,
    successful_concrete_barrier_data,
)
from tests.support.sqlite import closed_sqlite_connection

pytestmark = pytest.mark.unit


class _ReportProbe(DeepGenomeReportMixin):
    """Test-only mixin host exposing protected helpers via public names.

    The mixin's ``_preamble_and_analysis`` / ``_experiment_prompt`` /
    ``_summary_source_content`` helpers are protected because production
    code only calls them from sibling node methods on the same class.
    Tests exercise them through this subclass so the calls stay inside
    the class hierarchy.
    """

    def __init__(self) -> None:
        """Wire the single config attribute the protected helpers read."""
        self.deep_genome_config = DeepGenomeConfig()

    async def _dispatch_chat(
        self,
        user_query: str,
        locale: SupportedLocale | None = None,
    ) -> dict[str, Any]:
        """Return canned follow-up questions for final-report tests."""
        del user_query, locale
        return {"choices": [{"message": {"content": '["Q1?", "Q2?"]'}}]}

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

    async def run_synthesizer(self, state: DeepGenomeState) -> dict[str, Any]:
        """Public proxy for the concrete-outcome synthesis barrier."""
        return await self._run_report_synthesizer(state)

    async def run_follow_up_node(
        self, state: DeepGenomeState
    ) -> dict[str, Any]:
        """Public proxy for the final follow-up report node."""
        return await self._run_follow_up_node(state)

    async def run_protocol(self, state: DeepGenomeState) -> dict[str, Any]:
        """Public proxy for the protocol summary node."""
        return await self._run_report_protocol(state)

    async def run_discussion(self, state: DeepGenomeState) -> dict[str, Any]:
        """Public proxy for the discussion node."""
        return await self._run_report_discussion(state)

    async def run_summary(self, state: DeepGenomeState) -> dict[str, Any]:
        """Public proxy for the conclusion node."""
        return await self._run_report_summary(state)

    async def experiment_protocols(
        self, experiments: list[Any], locale: SupportedLocale | None = None
    ) -> str:
        """Public proxy for protocol retrieval."""
        return await self._experiment_protocols(experiments, locale)

    async def _dispatch_knowledge_retrieve(
        self,
        user_query: str,
        repo_id_dict: dict[str, int],
        locale: SupportedLocale | None = None,
    ) -> dict[str, Any]:
        """Return canned protocol text for experiment-protocol tests."""
        del repo_id_dict, locale
        return {
            "choices": [{"message": {"content": f"protocol-for-{user_query}"}}]
        }


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


async def test_synthesizer_rejects_all_terminal_failures() -> None:
    """No usable concrete result raises a fixed workflow error."""
    state = _state(
        work_items=concrete_barrier_work_items(),
        raw_analyst_data=failed_concrete_barrier_data(),
    )

    with pytest.raises(
        DeepGenomeWorkflowError, match="^no usable analysis result$"
    ):
        await _ReportProbe().run_synthesizer(state)


async def test_synthesizer_waits_for_missing_concrete_outcome() -> None:
    """Missing planned rows keep synthesis pending without a sticky flag."""
    state = _state(
        work_items=concrete_barrier_work_items(),
        raw_analyst_data=successful_concrete_barrier_data(),
    )

    assert await _ReportProbe().run_synthesizer(state) == {}


async def test_synthesizer_rejects_missing_final_synthesis() -> None:
    """Terminal outcomes without renderable sections cannot complete."""
    state = _state(
        synthesize_report=None,
        analyst_summaries={},
        work_items=concrete_barrier_work_items(),
        raw_analyst_data=partially_failed_concrete_barrier_data(),
    )

    with pytest.raises(
        DeepGenomeWorkflowError, match="^final synthesis unavailable$"
    ):
        await _ReportProbe().run_synthesizer(state)


def _finalization_fixture(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, str, str, int]:
    """Build a reserved run with a running finalization barrier."""
    db_path = tmp_path / "tasks.db"
    reservation = reserve_smep_finalization(str(db_path))
    store = DeepGenomeStore(str(db_path))
    snapshot = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="SMEP summary",
    )
    return (
        store,
        str(db_path),
        reservation.umbrella_task_id,
        snapshot.report_revision,
    )


def _report_state(task_id: str, report_dir: Path) -> DeepGenomeState:
    """Build the final-node state bound to one reserved umbrella."""
    return _state(
        task_id=task_id,
        report_dir=str(report_dir),
        part12_combined=(
            "# Deep Genome Analysis of Os01g0177400\n\n"
            "## Gene Profiles\n\nprofile\n\n"
            "## Bioinformatic Analysis and Molecular Design\n\nanalysis"
        ),
        summary_report="conclusion",
        follow_up_questions=[],
    )


async def test_run_follow_up_node_publishes_assembled_report_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final node publishes Markdown and settles the owner run."""
    store, db_path, task_id, _ = _finalization_fixture(tmp_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    report_dir = tmp_path / "report"
    report_dir.mkdir()

    out = await _ReportProbe().run_follow_up_node(
        _report_state(task_id, report_dir)
    )

    snapshot = store.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.status == "succeeded"
    assert snapshot.final_report is not None
    assert "## Follow up questions:" in snapshot.final_report
    assert (report_dir / "Os01g0177400_report.md").exists()
    with closed_sqlite_connection(db_path) as conn:
        run_status = conn.execute(
            "SELECT status FROM runs WHERE run_id = 'run-1'"
        ).fetchone()[0]
    assert run_status == "succeeded"
    assert out["follow_up_questions"] == ["Q1?", "Q2?"]


async def test_follow_up_failure_preserves_intermediate_and_fails_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synthesis exception cannot be projected as a successful run."""
    store, db_path, task_id, _ = _finalization_fixture(tmp_path)
    before = store.get_snapshot(task_id)
    assert before is not None
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    report_dir = tmp_path / "report"
    report_dir.mkdir()

    async def fail_dispatch(_user_query: str) -> dict[str, Any]:
        """Raise the same typed failure category as a provider outage."""
        raise RuntimeError("provider unavailable")

    probe = _ReportProbe()
    setattr(probe, "_dispatch_chat", fail_dispatch)

    with pytest.raises(
        DeepGenomeWorkflowError, match="^final synthesis failed$"
    ):
        await probe.run_follow_up_node(_report_state(task_id, report_dir))

    snapshot = store.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.intermediate_report == before.intermediate_report
    assert snapshot.final_report is None
    with closed_sqlite_connection(db_path) as conn:
        run = conn.execute(
            "SELECT status, error FROM runs WHERE run_id = 'run-1'"
        ).fetchone()
    assert run == ("failed", "final synthesis failed")


async def test_follow_up_rejects_empty_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty final report fails the owner while retaining intermediate."""
    store, db_path, task_id, _ = _finalization_fixture(tmp_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(report_module, "_assemble_final_report", lambda _: "")
    report_dir = tmp_path / "report"
    report_dir.mkdir()

    with pytest.raises(
        DeepGenomeWorkflowError, match="^final report unavailable$"
    ):
        await _ReportProbe().run_follow_up_node(
            _report_state(task_id, report_dir)
        )

    snapshot = store.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.final_report is None


async def test_follow_up_requires_durable_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final node without its umbrella id fails closed."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    report_dir = tmp_path / "report"
    report_dir.mkdir()

    with pytest.raises(
        DeepGenomeWorkflowError, match="^final report tracking unavailable$"
    ):
        await _ReportProbe().run_follow_up_node(
            _state(task_id=None, report_dir=str(report_dir))
        )


def test_section_has_data_and_assemble_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section skipping, figure rewrite, and analyst-off layout stay stable."""
    assert not _section_has_data({"tree_path": ""}, ("tree_path",))
    assert not _section_has_data({"tree_path": "None Results"}, ("tree_path",))
    assert _section_has_data({"tree_path": "/tmp/tree.png"}, ("tree_path",))
    captured: list[dict[str, Any]] = []

    def fake_prompt(_file: Any, path: str, data: dict[str, Any]) -> str:
        captured.append({"path": path, **data})
        return f"{path}:{data['section_number']}"

    monkeypatch.setattr(report_module, "get_prompt", fake_prompt)
    body = _assemble_sections(
        {
            "single_cell_summary": "cells",
            "single_cell_legend": "See Figure 9 for details",
            "domain_table": "table",
            "domain_summary": "domains",
            "domain_legend": "Figure 3 domains",
        },
        "prompts.yaml",
    )
    assert "section_single_cell:1" in body
    report = _assemble_final_report(
        _state(
            config_params={"use_analyst_agent": False},
            part12_combined="HEAD",
            discussion_report="D",
            summary_report="S",
        )
    )
    assert "## Recommended experiments" not in report


async def test_write_async_and_public_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write failures surface; public wrappers unwrap chat/knowledge."""

    def boom(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(report_module, "_write", boom)
    with pytest.raises(OSError, match="disk full"):
        await getattr(report_module, "_write_async")(
            tmp_path / "out.md", "text"
        )

    class _LiveProbe(DeepGenomeReportMixin):
        def __init__(self) -> None:
            async def knowledge_invoke(
                payload: dict[str, Any],
            ) -> dict[str, Any]:
                del payload
                return {"final_response": {"ok": True}}

            self._agents = SimpleNamespace(
                knowledge_app=SimpleNamespace(ainvoke=knowledge_invoke)
            )

        def _chat_kwargs(self) -> dict[str, str]:
            return {"model": "phyto"}

    async def fake_ainvoke(payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        return {"raw": True}

    monkeypatch.setattr(
        report_module,
        "_cached_chat_app",
        lambda: SimpleNamespace(ainvoke=fake_ainvoke),
    )
    monkeypatch.setattr(
        report_module, "build_chat_input", lambda **kwargs: kwargs
    )
    monkeypatch.setattr(
        report_module,
        "extract_chat_response",
        lambda output: {"unwrapped": output},
    )
    probe = _LiveProbe()
    assert await probe.dispatch_chat("hello", "en-US") == {
        "unwrapped": {"raw": True}
    }
    assert await probe.dispatch_knowledge_retrieve(
        "protocol", {"repo": 3}, "en-US"
    ) == {"ok": True}


async def test_synthesizer_and_experiment_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip, write, wait, and experiment-design paths stay deterministic."""
    assert await _ReportProbe().run_synthesizer(
        _state(skip_synthesize=True)
    ) == {"experiment_completed_branches": 1}
    monkeypatch.setattr(
        report_module, "resolve_scratch_dir", lambda *a, **k: str(tmp_path)
    )
    monkeypatch.setattr(
        report_module, "get_prompt", lambda *a, **k: "SECTION-BODY"
    )

    async def capture(path: Path, text: str) -> None:
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    monkeypatch.setattr(report_module, "_write_async", capture)
    result = await _ReportProbe().run_synthesizer(
        _state(
            work_items=concrete_barrier_work_items(),
            raw_analyst_data=partially_failed_concrete_barrier_data(),
            analyst_summaries={"single_cell_summary": "cells"},
        )
    )
    assert result["experiment_completed_branches"] == 1
    probe = _ReportProbe()
    assert await probe.run_experiment(_state(synthesize_report=None)) == {}
    assert await probe.run_experiment(_state(report_triggered=True)) == {}
    designed = await probe.run_experiment(_state(report_triggered=False))
    assert designed["report_triggered"] is True
    assert "protocol-for-CRISPR" in await probe.experiment_protocols(
        ["CRISPR"]
    )


async def test_protocol_discussion_summary_and_finalization_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report nodes unwrap chat; store faults fail the owner closed."""
    monkeypatch.setattr(report_module, "get_prompt", lambda *a, **k: "PROMPT")
    probe = _ReportProbe()
    assert (await probe.run_protocol(_state()))["protocol_report"]
    assert (await probe.run_discussion(_state()))["discussion_report"]
    assert (
        await probe.run_discussion(
            _state(config_params={"use_analyst_agent": False})
        )
    )["discussion_report"]
    assert (await probe.run_summary(_state()))["summary_report"]
    db_path = str(tmp_path / "empty.db")
    DeepGenomeStore(db_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    with pytest.raises(
        DeepGenomeWorkflowError, match="^final report tracking unavailable$"
    ):
        await _ReportProbe().run_follow_up_node(
            _state(task_id="missing-task", report_dir=str(tmp_path))
        )
    _store, db_path, task_id, _ = _finalization_fixture(tmp_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.report.resolve_tasks_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        report_module.DeepGenomeStore,
        "publish_final_report",
        lambda *_a, **_k: (_ for _ in ()).throw(
            DeepGenomeTransitionError("conflict")
        ),
    )
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    with pytest.raises(
        DeepGenomeWorkflowError, match="^final report publication failed$"
    ):
        await _ReportProbe().run_follow_up_node(
            _report_state(task_id, report_dir)
        )
    monkeypatch.setattr(report_module, "_assemble_final_report", lambda _: "")
    monkeypatch.setattr(
        report_module.DeepGenomeStore,
        "fail_umbrella",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.Error("locked")),
    )
    with pytest.raises(
        DeepGenomeWorkflowError, match="^final report tracking failed$"
    ):
        await _ReportProbe().run_follow_up_node(
            _report_state(task_id, report_dir)
        )
