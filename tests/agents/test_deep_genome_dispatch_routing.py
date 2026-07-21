# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Routing tests for the deep_genome producer-wrapper reroute.

Pins ``DeepGenomeDispatchMixin`` routing: ``evolution_analysis`` fans to
the mounted ``evolution_node``; ``protein_structure_analysis`` /
``promoter_analysis`` tasks call the matching design module wrappers
inside ``_submit_analysis_task``. Non-transferred analysis types route
through ``submit_analyst_via_subgraph``.
"""

# The direct routing and coordinator probes below target internal dispatch
# seams; each carries a symbol-scoped protected-access directive.

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.deep_genome import dispatch as dispatch_module
from mcp_server_phytomni.agents.deep_genome import routing as routing_module
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    DeepGenomeWorkflowError,
    RemoteSubmission,
    WorkItemOutcome,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.graphs import analyst_dispatch_adapters

pytestmark = pytest.mark.agent


def _build_mixin_instance() -> Any:
    """Construct a minimal stand-in for ``DeepGenomeDispatchMixin``.

    The dispatch mixin only reads ``self.deep_genome_config`` and
    ``self._agents.analyst_agent`` inside ``_submit_analysis_task``;
    a ``SimpleNamespace`` with those two attributes is enough to
    exercise the routing branch without constructing the full
    ``DeepGenomeAgents`` (which would compile a graph and bind a
    ``BriefGeneAgent`` subgraph).
    """
    return SimpleNamespace(
        deep_genome_config=DeepGenomeConfig(),
        sensitive_config=SimpleNamespace(),
        _agents=SimpleNamespace(analyst_agent="analyst-stub"),
        _analysis_prompt_parts=lambda _ctx: (
            "goal-stub",
            ["data-stub"],
            "meta-stub",
            "small",
        ),
    )


def _context(analysis_type: str) -> AnalysisDispatchContext:
    """Build a dispatch context for a single-gene single-task submit."""
    return AnalysisDispatchContext(
        analysis_type=analysis_type,
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )


def _install_shared_helper_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch the shared dispatch helper on the dispatch module.

    The non-transferred (12 remaining) analysis types always route
    through ``submit_analyst_via_subgraph`` since the cluster #9
    sunset removed deep_genome's inline ``analyst_agent.arun`` call.
    """
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "caller-1",
            "source_task_id": "remote-1",
            "output_dir": "/obs/subgraph",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        dispatch_module, "submit_analyst_via_subgraph", subgraph_mock
    )
    return subgraph_mock


def _install_wrapper_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, AsyncMock]:
    """Patch the 2 module-level design producer wrappers on dispatch.

    Evolution is no longer a producer-wrapper branch inside
    ``_submit_analysis_task``; it is routed to the mounted
    ``evolution_node`` instead, so only the two design wrappers remain.
    """
    mocks = {
        "protein_structure_for_gene": AsyncMock(
            return_value={
                "task_id": "struct-id",
                "output_dir": "/obs/struct",
                "task_status": "SUCCEEDED",
            }
        ),
        "promoter_design_for_gene": AsyncMock(
            return_value={
                "task_id": "prom-id",
                "output_dir": "/obs/prom",
                "task_status": "SUCCEEDED",
            }
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(dispatch_module, name, mock)
    return mocks


def test_route_analyst_tasks_sends_evolution_to_evolution_node() -> None:
    """The evolution task fans to evolution_node; others to analyst_node.

    Evolution stays an entry in ``analysis_tasks`` (so the synthesize
    barrier's ``total_expected`` count is unchanged) but routes to the
    dedicated mounted ``evolution_node`` rather than the generic
    ``analyst_node``.
    """
    # pylint: disable=protected-access
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": "evolution_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
            {
                "analysis_type": "single_cell_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    targets = {send.node for send in sends}
    assert targets == {"evolution_node", "single_cell_node"}
    evo = next(send for send in sends if send.node == "evolution_node")
    assert evo.arg["analysis_type"] == "evolution_analysis"
    assert evo.arg["task_index"] == 0


def test_route_start_waits_for_brief_gene_before_task_preparation() -> None:
    """The initial route launches only the required BriefGene mount."""
    # pylint: disable=protected-access
    state: Any = {"config_params": {"use_analyst_agent": True}}

    sends = dispatch_module.DeepGenomeDispatchMixin._route_start(
        object(), state
    )

    assert sends == ["brief_gene_node"]


def test_route_after_brief_gene_reaches_preparation_only_when_enabled() -> (
    None
):
    """Successful BriefGene gates optional analyst preparation."""
    # pylint: disable=protected-access
    route = dispatch_module.DeepGenomeDispatchMixin._route_after_brief_gene

    enabled_state: Any = {"config_params": {"use_analyst_agent": True}}
    disabled_state: Any = {"config_params": {"use_analyst_agent": False}}
    assert route(object(), enabled_state) == [
        "prepare_tasks_node",
        "experiment_node",
    ]
    assert route(object(), disabled_state) == "experiment_node"


def test_route_experiment_skips_protocol_when_analyst_disabled() -> None:
    """The analyst-off path proceeds to discussion without looping."""
    # pylint: disable=protected-access
    state: Any = {
        "config_params": {"use_analyst_agent": False},
        "report_triggered": True,
    }

    route = dispatch_module.DeepGenomeDispatchMixin._route_experiment_barrier

    assert route(object(), state) == "discussion_node"


def test_route_synthesize_waits_for_every_concrete_work_item() -> None:
    """The synthesis route uses twelve concrete rows, not a branch count."""
    # pylint: disable=protected-access
    state: Any = {
        "work_items": [
            {
                "work_item_key": "evolution_analysis",
                "analysis_type": "evolution_analysis",
            },
            {
                "work_item_key": "promoter_design",
                "analysis_type": "promoter_design_analysis",
                "section_key": "digital_design",
            },
        ],
        "raw_analyst_data": {
            "task_0:evolution_analysis": {
                "analysis_type": "evolution_analysis",
                "status": "success",
            }
        },
    }

    route = dispatch_module.DeepGenomeDispatchMixin._route_synthesize_barrier

    assert route(object(), state) == "synthesize_node"


def test_route_synthesize_rejects_all_terminal_failures() -> None:
    """The all-failed concrete matrix raises instead of reaching END."""
    # pylint: disable=protected-access
    state: Any = {
        "work_items": [
            {
                "work_item_key": "evolution_analysis",
                "analysis_type": "evolution_analysis",
            },
            {
                "work_item_key": "promoter_design",
                "analysis_type": "promoter_design_analysis",
                "section_key": "digital_design",
            },
        ],
        "raw_analyst_data": {
            "task_0:evolution_analysis": {
                "analysis_type": "evolution_analysis",
                "status": "failed",
            },
            "task_10": {
                "analysis_type": "digital_design",
                "status": "failed",
            },
        },
    }

    route = dispatch_module.DeepGenomeDispatchMixin._route_synthesize_barrier

    with pytest.raises(
        DeepGenomeWorkflowError, match="^no usable analysis result$"
    ):
        route(object(), state)


def test_route_synthesize_preserves_skip_fixture() -> None:
    """Pre-rendered test-mode synthesis bypasses concrete task rows."""
    # pylint: disable=protected-access
    state: Any = {
        "skip_synthesize": True,
        "synthesize_report": "pre-rendered synthesis",
    }

    route = dispatch_module.DeepGenomeDispatchMixin._route_synthesize_barrier

    assert route(object(), state) == "experiment_node"


def test_route_analyst_tasks_sends_design_to_design_node() -> None:
    """The digital_design task fans to the mounted ``design_node``."""
    # pylint: disable=protected-access
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": "digital_design",
                "target_gene": "g1",
                "species_code": "osa",
            },
            {
                "analysis_type": "single_cell_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    targets = {send.node for send in sends}
    assert targets == {"design_node", "single_cell_node"}
    design = next(send for send in sends if send.node == "design_node")
    assert design.arg["analysis_type"] == "digital_design"
    assert design.arg["task_index"] == 0


def test_route_analyst_tasks_sends_each_generic_to_its_own_node() -> None:
    """Each generic analysis_type fans to its deterministic named node."""
    # pylint: disable=protected-access
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": analysis_type,
                "target_gene": "g1",
                "species_code": "osa",
            }
            for analysis_type in dispatch_module.GENERIC_ANALYSIS_NODE_TYPES
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    by_type = {send.arg["analysis_type"]: send.node for send in sends}
    assert by_type == {
        analysis_type: dispatch_module._analyst_node_name(analysis_type)
        for analysis_type in dispatch_module.GENERIC_ANALYSIS_NODE_TYPES
    }
    assert "analyst_node" not in {send.node for send in sends}


async def test_protein_structure_routes_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``protein_structure_analysis`` routes structure to its wrapper."""
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("protein_structure_analysis")
        )
    )

    assert isinstance(result, RemoteSubmission)
    assert result.submitted_task_id == "struct-id"
    wrappers["protein_structure_for_gene"].assert_awaited_once_with(
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
        is_polling=False,
    )
    subgraph_mock.assert_not_awaited()


async def test_promoter_routes_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``promoter_analysis`` routes promoter to its wrapper."""
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("promoter_analysis")
        )
    )

    assert isinstance(result, RemoteSubmission)
    assert result.submitted_task_id == "prom-id"
    wrappers["promoter_design_for_gene"].assert_awaited_once_with(
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
        is_polling=False,
    )
    subgraph_mock.assert_not_awaited()


async def test_non_transferred_type_routes_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-transferred types route through ``submit_analyst_via_subgraph``.

    Pins cluster #9 sunset: the 12 remaining analysis types share
    the same ``submit_analyst_via_subgraph`` chokepoint as design /
    network / research / environment / evolution.
    """
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert isinstance(result, RemoteSubmission)
    assert result.poll_task_id == "remote-1"
    assert result.submitted_task_id == "caller-1"
    subgraph_mock.assert_awaited_once()
    for wrapper in wrappers.values():
        wrapper.assert_not_awaited()


async def test_deep_genome_generic_dispatch_is_submit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepGenome submits generic work without nested polling.

    The submission acknowledgement is normalized immediately so the
    coordinator can poll the effective remote task id later.
    """
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert subgraph_mock.await_args is not None
    assert subgraph_mock.await_args.kwargs["is_polling"] is False
    assert isinstance(result, RemoteSubmission)
    assert result.poll_task_id == "remote-1"


async def test_dispatch_coordinator_receives_effective_poll_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Poll the dedup source id and resolve Markdown before local success."""
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    mixin.deep_genome_config.TIMEOUT = 4.0
    mixin.deep_genome_config.POLL_INTERVAL = 2.0
    mixin.deep_genome_config.MAX_POLL = 10.0
    submission = RemoteSubmission(
        submitted_task_id="caller-1",
        poll_task_id="remote-1",
        output_dir="/obs/out",
    )
    submit = AsyncMock(return_value=submission)
    download_dir = tmp_path / "results"
    download_dir.mkdir()
    (download_dir / "analysis.summary").write_text(
        "# usable result\n",
        encoding="utf-8",
    )
    download = AsyncMock(return_value=str(download_dir))
    monkeypatch.setattr(mixin, "_submit_analysis_task", submit, raising=False)
    monkeypatch.setattr(
        mixin,
        "_download_analysis_result",
        download,
        raising=False,
    )
    status = AsyncMock(return_value={"status": "SUCCEEDED"})
    monkeypatch.setattr(dispatch_module, "task_status", status)
    seen: dict[str, Any] = {}

    async def poll(
        received: RemoteSubmission,
        *,
        status_reader,
        result_resolver,
        transition_sink,
        **_kwargs: Any,
    ) -> WorkItemOutcome:
        """Exercise status and result seams rather than submission success."""
        seen["poll_task_id"] = received.poll_task_id
        remote_status = await status_reader(received.poll_task_id, 4.0)
        assert remote_status["status"] == "SUCCEEDED"
        summary = await result_resolver(received)
        assert summary == "# usable result"
        return await transition_sink("succeeded", summary, None)

    monkeypatch.setattr(dispatch_module, "poll_work_item", poll)
    poll_remote = getattr(
        dispatch_module.DeepGenomeDispatchMixin, "_poll_remote_submission"
    )
    setattr(mixin, "_poll_remote_submission", partial(poll_remote, mixin))

    dispatch_and_wait = (
        dispatch_module.DeepGenomeDispatchMixin._dispatch_and_wait_analysis
    )
    result = await dispatch_and_wait(
        mixin,
        "haplotypes_analysis",
        "ath",
        "AT1G01010",
    )

    assert seen == {"poll_task_id": "remote-1"}
    assert result["status"] == "completed"
    submit.assert_awaited_once()
    status.assert_awaited_once()
    status_call = status.await_args
    assert status_call is not None
    assert status_call.args == ("remote-1",)
    assert status_call.kwargs["timeout"] == 4.0
    download.assert_awaited_once()


async def test_default_analyst_adapter_still_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone adapter default keeps its historical polling mode."""
    captured: list[bool] = []

    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "prepare_analyst_dispatch_context",
        lambda *_args: SimpleNamespace(
            analysis_type="standalone",
            output_dir="/obs/out",
            thread_id="thread-1",
        ),
    )

    async def no_reuse(*_args: Any, **_kwargs: Any) -> None:
        """Keep the adapter on its fresh-submission path."""
        return None

    monkeypatch.setattr(
        analyst_dispatch_adapters, "_reuse_prior_dispatch", no_reuse
    )

    def capture_input(payload: Any, *, is_polling: bool = True) -> dict:
        """Record the adapter's default polling argument."""
        del payload
        captured.append(is_polling)
        return {}

    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "map_send_payload_to_analyst_input",
        capture_input,
    )
    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "record_dispatch_submission",
        lambda *_args, **_kwargs: None,
    )
    agent = SimpleNamespace(
        app=SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={
                    "task_id": "standalone-1",
                    "output_dir": "/obs/out",
                }
            )
        )
    )

    await analyst_dispatch_adapters.submit_analyst_via_subgraph(
        agent,
        SimpleNamespace(USER_ID="alice"),
        SimpleNamespace(),
        {
            "analysis_type": "standalone",
            "target_id": "gene-1",
            "prompt_parts": ("goal", "meta", {}),
            "compute_resource": "small",
        },
    )

    assert captured == [True]


async def test_prepare_tasks_includes_protein_structure() -> None:
    """``_prepare_analysis_tasks`` enumerates a protein-structure task.

    The producer + ``load_protein_structure`` loader already exist; the
    task was simply never added to the analysis list, so the §Protein
    Structure section never rendered. ``species_code="ath"`` takes the
    ``case _`` branch and avoids the BI id-table lookup.
    """
    # pylint: disable=protected-access
    mixin = _build_mixin_instance()
    state: Any = {"gene_id": "AT1G01010", "species_code": "ath"}

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._prepare_analysis_tasks(
            mixin, state
        )
    )

    types = {task["analysis_type"] for task in result["analysis_tasks"]}
    assert "protein_structure_analysis" in types


def test_transferred_types_dropped_from_prompt_maps() -> None:
    """Producer-owned types leave the goal / meta prompt maps.

    evolution_analysis / protein_structure_analysis / promoter_analysis
    submit through the evolution and design producer wrappers (the
    routing tests above), so deep_genome no longer builds their goal or
    meta prompts and those two maps must not list them. The shared
    ANALYSIS_DATA_LIST_MAP still keeps protein_structure_analysis (its
    data lives under a non-identity key, structure_analysis, that the
    producer reuses via resolve_data_list_key); evolution and promoter
    are identity lookups and need no entry. Output-file features stay
    because deep_genome still downloads and summarizes the producer's
    results by analysis type.
    """
    transferred = {
        "evolution_analysis",
        "protein_structure_analysis",
        "promoter_analysis",
    }
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_GOAL_TEMPLATE_MAP)
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_META_TEMPLATE_MAP)
    # protein_structure_analysis is the one transferred type whose data
    # lives under a DIFFERENT key (structure_analysis), so it stays in
    # the shared ANALYSIS_DATA_LIST_MAP translation table; evolution and
    # promoter are identity lookups and need no entry.
    assert {"evolution_analysis", "promoter_analysis"}.isdisjoint(
        dispatch_module.ANALYSIS_DATA_LIST_MAP
    )
    assert (
        dispatch_module.ANALYSIS_DATA_LIST_MAP["protein_structure_analysis"]
        == "structure_analysis"
    )
    assert transferred <= set(dispatch_module.ANALYSIS_TARGET_FILE_FEATURE_MAP)


@pytest.mark.parametrize(
    ("analysis_type", "expected_node"),
    [
        ("evolution_analysis", "evolution_node"),
        ("digital_design", "design_node"),
        ("single_cell_analysis", "single_cell_node"),
        ("smep_analysis", "smep_node"),
    ],
)
def test_routing_table_maps_special_and_generic_nodes(
    analysis_type: str, expected_node: str
) -> None:
    """The pure routing table keeps mounted and worker destinations stable."""
    assert (
        routing_module.node_for_analysis_type(analysis_type) == expected_node
    )


def test_routing_sends_preserve_order_identity_and_work_item_keys() -> None:
    """Send creation carries identity without mutating source state."""
    state: dict[str, Any] = {
        "task_submit_sleep": 3,
        "task_id": "umbrella-1",
        "run_id": "run-1",
        "owner": "alice",
        "output_dir": "/obs/umbrella-1",
        "analysis_tasks": [
            {"analysis_type": "smep_analysis", "target_gene": "g1"},
            {"analysis_type": "digital_design", "target_gene": "g1"},
        ],
        "work_items": [
            {
                "section_key": "smep_analysis",
                "work_item_key": "smep_analysis",
                "display_order": 7,
            },
            {
                "section_key": "digital_design",
                "work_item_key": "protein_design",
                "display_order": 10,
            },
        ],
    }

    sends = routing_module.build_analyst_sends(state)

    assert [send.node for send in sends] == ["smep_node", "design_node"]
    assert [send.arg["task_index"] for send in sends] == [0, 1]
    assert [send.arg["task_submit_sleep"] for send in sends] == [0, 3]
    assert sends[0].arg["work_item_key"] == "smep_analysis"
    assert sends[1].arg["work_item_key"] == "protein_design"
    assert all(send.arg["owner"] == "alice" for send in sends)
    assert state["analysis_tasks"][0]["analysis_type"] == "smep_analysis"


def test_routing_sends_skip_empty_and_keep_duplicate_task_indices() -> None:
    """Skipped work emits no Send; duplicate logical keys remain distinct."""
    duplicate_tasks = [
        {"analysis_type": "smoc_analysis", "target_gene": "g1"},
        {"analysis_type": "smoc_analysis", "target_gene": "g2"},
    ]
    sends = routing_module.build_analyst_sends(
        {"analysis_tasks": duplicate_tasks, "task_submit_sleep": 0}
    )

    assert [send.arg["task_index"] for send in sends] == [0, 1]
    assert [send.arg["target_gene"] for send in sends] == ["g1", "g2"]
    assert not routing_module.build_analyst_sends({"analysis_tasks": []})


def test_routing_prompt_parts_split_data_subtitle_and_propagate_lookup_errors(
    tmp_path,
) -> None:
    """Prompt preparation is deterministic and exposes loader failures."""
    context = routing_module.AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/out",
    )
    prompt_calls: list[tuple[str, str, object]] = []

    def fake_prompt(path: str, template: str, parameters=None) -> str:
        prompt_calls.append((path, template, parameters))
        return template

    data_calls: list[tuple[str, str, str]] = []

    def fake_data(path: str, key: str, species: str) -> dict[str, Any]:
        data_calls.append((path, key, species))
        return {"tissues": {"/obs/a": "description"}}

    result = routing_module.build_analysis_prompt_parts(
        context,
        prompt_file=str(tmp_path / "prompts.yaml"),
        data_file=str(tmp_path / "data.json"),
        prompt_loader=fake_prompt,
        data_loader=fake_data,
    )

    assert result == (
        "user/gene_expression_analysis/tissue",
        {"/obs/a": "description"},
        "user/gene_expression_analysis_meta",
        "small",
    )
    assert prompt_calls[0][2] == {"gene_id": "AT1G01010"}
    assert data_calls == [
        (str(tmp_path / "data.json"), "gene_expression_analysis", "ath")
    ]

    def failing_data(*_args: object) -> dict[str, Any]:
        raise KeyError("missing species")

    with pytest.raises(KeyError, match="missing species"):
        routing_module.build_analysis_prompt_parts(
            context,
            prompt_file="prompts.yaml",
            data_file="data.json",
            prompt_loader=fake_prompt,
            data_loader=failing_data,
        )


def test_routing_prompt_parts_reject_unknown_type_before_loaders() -> None:
    """Unknown analysis slugs fail before prompt or metadata I/O."""
    context = routing_module.AnalysisDispatchContext(
        analysis_type="unknown_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/out",
    )
    with pytest.raises(ValueError, match="unknown_analysis"):
        routing_module.build_analysis_prompt_parts(
            context,
            prompt_file="prompts.yaml",
            data_file="data.json",
            prompt_loader=lambda *_args, **_kwargs: pytest.fail("prompt load"),
            data_loader=lambda *_args: pytest.fail("data load"),
        )


def test_routing_target_file_lookup_returns_copy_and_default() -> None:
    """Output feature lookup is isolated from mutable caller changes."""
    features = routing_module.target_file_features("smoc_analysis")
    features.append("caller-only")

    assert "caller-only" not in routing_module.target_file_features(
        "smoc_analysis"
    )
    assert routing_module.target_file_features("not-registered") == [
        ".png",
        ".summary",
        ".legend",
    ]
