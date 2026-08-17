# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst dispatch IO adapters.

Pins ``map_send_payload_to_analyst_input`` and
``map_analyst_output_to_dispatch_state`` against the dispatch contract.
Also covers the fingerprint-threading fix so
``prepare_analyst_dispatch_context`` receives the fingerprint before
creating the output directory.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import mcp_server_phytomni.agents.analyst.graph as _analyst_graph_mod
import mcp_server_phytomni.agents.shared.analysis as _analysis_mod
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.agents.analyst.state import AnalystInput, AnalystState
from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    map_analyst_output_to_dispatch_state,
    map_send_payload_to_analyst_input,
)
from mcp_server_phytomni.runtime.task_dedup import analyst_task_fingerprint
from mcp_server_phytomni.storage.path_policy import RunIdentity

from ._analyst_fakes import fake_submitting_agent

pytestmark = pytest.mark.agent


def _sample_payload() -> dict:
    return {
        "analysis_type": "design",
        "target_id": "AT1G01010",
        "prompt_parts": (
            "Design a CRISPR knockout strategy for AT1G01010.",
            "preset-plan-meta-blob",
            {"sample_a.tsv": "expression matrix"},
        ),
        "compute_resource": "medium",
        "output_dir": "/obs/phytomni/runs/design/abc",
    }


def test_map_send_payload_returns_analyst_input_field_set() -> None:
    """Adapter populates the AnalystInput contract minus ``obs_file_list``.

    Derives the expected key set directly from
    ``AnalystInput.__optional_keys__`` plus the required ``query``,
    minus ``obs_file_list`` which ``submit_analyst_analysis`` never
    threads into the request. A future contract change to
    ``AnalystInput`` propagates here without re-typing the literal
    field list.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    optional_keys = set(getattr(AnalystInput, "__optional_keys__", set()))
    expected_optionals = optional_keys - {
        "obs_file_list",
        "dispatch_fingerprint",
        "input_fingerprint",
        "research_grant_sidecar",
    }
    assert set(result.keys()) == expected_optionals | {"query"}


def test_result_child_flag_is_retained_by_analyst_state() -> None:
    """The full graph state keeps the optional dispatch ownership flag."""
    optional_keys = set(getattr(AnalystState, "__optional_keys__", set()))

    assert "output_dir_is_result_child" in optional_keys


def test_map_send_payload_unpacks_prompt_parts() -> None:
    """``prompt_parts`` 3-tuple unpacks into goal / preset_plan / data_list.

    Matches the ``submit_analyst_analysis`` unpack
    (``goal_description, meta, data_list = request["prompt_parts"]``)
    so dispatch-side request shaping stays the source of truth.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["goal_description"] == (
        "Design a CRISPR knockout strategy for AT1G01010."
    )
    assert result["preset_plan"] == "preset-plan-meta-blob"
    assert result["data_list"] == {"sample_a.tsv": "expression matrix"}


def test_map_send_payload_forwards_document_context_when_present() -> None:
    """Research documents reach the Analyst subgraph when supplied."""
    payload = _sample_payload()
    payload["obs_file_list"] = ["obs://private/paper.pdf"]

    result = dict(map_send_payload_to_analyst_input(payload))

    assert result["obs_file_list"] == ["obs://private/paper.pdf"]


def test_map_send_payload_pins_auto_select_and_preset_plan_constants() -> None:
    """``is_auto_select`` and ``is_preset_plan`` stay pinned constants.

    ``is_auto_select=False`` (the data list is preset) and
    ``is_preset_plan=True`` (the preset plan bypasses the retrieval +
    critic path) are pinned regardless of caller. Only ``is_polling``
    is parameterised — see the polling-specific tests below.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["is_auto_select"] is False
    assert result["is_preset_plan"] is True


def test_map_send_payload_is_polling_defaults_to_true() -> None:
    """Default ``is_polling=True`` is forward-looking for deep_genome.

    Pins the producer-side default so a deep_genome caller that
    omits ``is_polling`` inherits the polling semantics its current
    ``dispatch.py:_submit_analysis_task`` already uses (``arun(...,
    is_polling=True)``).
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["is_polling"] is True


def test_map_send_payload_is_polling_false_when_explicit() -> None:
    """Existing five consumers pass ``is_polling=False`` explicitly.

    Design / network / research / environment / evolution all keep
    fire-and-poll-elsewhere semantics; an explicit ``False`` kwarg
    must override the producer-side default so their
    ``submit_analyst_analysis`` / ``analyst.submit`` paths stay
    behaviour-preserved when routed through the subgraph.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload, is_polling=False))
    assert result["is_polling"] is False


def test_map_send_payload_threads_compute_resource_and_output_dir() -> None:
    """``compute_resource`` and ``output_dir`` flow through unchanged.

    The dispatch layer owns both decisions before calling the adapter,
    so the mapper must not rewrite either value.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["compute_resource"] == "medium"
    assert result["output_dir"] == "/obs/phytomni/runs/design/abc"


def test_map_send_payload_defaults_missing_output_dir_to_empty_string() -> (
    None
):
    """A missing ``output_dir`` falls back to ``""`` rather than ``None``.

    Keeps the ``AnalystInput`` ``output_dir`` value typed as ``str``
    so downstream ``compatibility_config.OUTPUT_DIR`` access does not
    have to widen to ``Optional[str]``.
    """
    payload = _sample_payload()
    payload.pop("output_dir")
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["output_dir"] == ""


def test_map_send_payload_sets_query_to_empty_string() -> None:
    """``query`` is set to ``""`` (not ``None``) for preset-plan dispatch.

    ``AnalystInput.query`` is ``Required[str]``; the preset-plan
    branch of the analyst graph never reads ``query`` once
    ``is_preset_plan=True`` so an empty string carries no behavioural
    weight while keeping the type contract intact.
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["query"] == ""


def test_map_send_payload_raises_on_missing_prompt_parts() -> None:
    """A payload without ``prompt_parts`` raises ``KeyError`` loudly.

    Treats missing dispatch metadata as a programming error rather
    than a silent default, so the adapter never produces a malformed
    ``AnalystInput`` that would surface as an obscure mid-graph
    crash.
    """
    payload = _sample_payload()
    payload.pop("prompt_parts")
    with pytest.raises(KeyError, match="prompt_parts"):
        map_send_payload_to_analyst_input(payload)


def test_map_analyst_output_exposes_dispatch_consumption_keys() -> None:
    """All five dispatch-consumed keys ride through the output projection.

    ``capture_analysis_result`` reads ``task_id``; downstream
    formatters read the remaining four. A future analyst output the
    dispatch flow consumes shows up here before the consumer
    silently sees ``None``.
    """
    final_state = {
        "task_id": "task-123",
        "output_dir": "/obs/phytomni/runs/design/abc",
        "plan": "step 1; step 2; step 3",
        "tool_usages": "tool_a, tool_b",
        "task_status": "SUCCEEDED",
        "extracted_tools": ["tool_a", "tool_b"],
    }
    result = map_analyst_output_to_dispatch_state(final_state)
    assert set(result.keys()) == {
        "task_id",
        "output_dir",
        "plan",
        "tool_usages",
        "task_status",
    }


def test_map_analyst_output_passes_values_through_unchanged() -> None:
    """Each value in the projection is the source value verbatim.

    Pins the no-transformation contract: the adapter is a projection,
    not a coercion, so downstream consumers see exactly what the
    analyst graph emitted.
    """
    final_state = {
        "task_id": "task-123",
        "output_dir": "/obs/phytomni/runs/design/abc",
        "plan": "step 1; step 2",
        "tool_usages": "tool_a",
        "task_status": "SUCCEEDED",
    }
    result = map_analyst_output_to_dispatch_state(final_state)
    assert result["task_id"] == "task-123"
    assert result["output_dir"] == "/obs/phytomni/runs/design/abc"
    assert result["plan"] == "step 1; step 2"
    assert result["tool_usages"] == "tool_a"
    assert result["task_status"] == "SUCCEEDED"


def test_map_analyst_output_handles_missing_fields_as_none() -> None:
    """Missing output keys surface as ``None`` for failure-path flows.

    Pre-submit failures or graph errors may leave ``task_id`` /
    ``task_status`` absent. ``capture_analysis_result`` reads
    ``task_result.get("task_id")`` and tolerates ``None``, so the
    adapter must not raise when keys are absent.
    """
    final_state = {
        "output_dir": "/obs/phytomni/runs/design/abc",
        "tool_usages": "",
    }
    result = map_analyst_output_to_dispatch_state(final_state)
    assert result["task_id"] is None
    assert result["plan"] is None
    assert result["task_status"] is None
    assert result["output_dir"] == "/obs/phytomni/runs/design/abc"
    assert result["tool_usages"] == ""


def test_map_round_trip_preserves_dispatch_shape() -> None:
    """A send-payload + sample final-state pair round-trips cleanly.

    Composing the two mappers proves the adapter pair shares a
    self-consistent view of the dispatch contract: payload → input,
    input ignored, output → state-update, state-update keys match
    what ``capture_analysis_result`` reads.
    """
    payload = _sample_payload()
    analyst_input = map_send_payload_to_analyst_input(payload)
    assert "query" in analyst_input

    final_state = {
        "task_id": "task-round-trip",
        "output_dir": payload["output_dir"],
        "plan": "preset plan executed",
        "tool_usages": "tool_a",
        "task_status": "SUCCEEDED",
    }
    state_update = map_analyst_output_to_dispatch_state(final_state)
    assert state_update["task_id"] == "task-round-trip"
    assert state_update["output_dir"] == payload["output_dir"]


# Fingerprint-threading integration test


def _dispatch_request() -> dict[str, Any]:
    """Return a minimal dispatch request for fingerprint-threading tests."""
    return {
        "analysis_type": "design_analysis",
        "target_id": "AT1G01010",
        "prompt_parts": (
            "Design a promoter for AT1G01010.",
            "preset-plan-meta",
            {"/obs/data.fa": "fasta"},
        ),
        "compute_resource": "small",
    }


async def test_dispatch_seam_passes_fingerprint_to_output_dir_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatch seam computes the fingerprint before creating the dir.

    ``submit_analyst_via_subgraph`` must pass the dispatch fingerprint
    into ``prepare_analyst_dispatch_context`` so that
    ``ensure_analysis_output_dir`` routes the directory to the shared
    content-addressed key rather than a per-run user-scoped path.

    Seam: monkeypatch ``ensure_analysis_output_dir`` on the
    ``agents.shared.analysis`` module to capture the ``fingerprint``
    kwarg; run the real ``prepare_analyst_dispatch_context`` (not
    stubbed); assert the captured fingerprint matches the expected
    ``analyst_task_fingerprint`` digest for the request.
    """
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)

    captured: dict[str, Any] = {}

    async def fake_ensure_output_dir(
        _config: Any,
        _analysis_type: str,
        _output_dir: str | None,
        _run_identity: Any = None,
        **kwargs: Any,
    ) -> str:
        captured["fingerprint"] = kwargs.get("fingerprint")
        return "/obs/shared/captured/output"

    monkeypatch.setattr(
        _analysis_mod, "ensure_analysis_output_dir", fake_ensure_output_dir
    )

    config = SimpleNamespace(USER_ID="user-fp-test")
    sensitive_config = object()
    request = _dispatch_request()

    await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-fp"),
        config,
        sensitive_config,
        request,
        is_polling=False,
    )

    goal_description, _meta, data_list = request["prompt_parts"]
    expected_fp = analyst_task_fingerprint(
        goal_description=goal_description,
        data_list=data_list,
        obs_file_list=None,
    )
    assert (
        "fingerprint" in captured
    ), "ensure_analysis_output_dir was not called or captured no fingerprint"
    assert captured["fingerprint"] == expected_fp


async def test_dispatch_child_directory_skips_fingerprint_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scoped child directory bypasses shared fingerprint reuse."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))

    async def unexpected_reuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("child dispatch must not reuse a fingerprint")

    monkeypatch.setattr(ada, "_reuse_prior_dispatch", unexpected_reuse)
    request = {
        **_dispatch_request(),
        "output_dir": "/obs/run/children/part-001",
        "output_dir_is_result_child": True,
    }

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-child"),
        SimpleNamespace(USER_ID="user-child"),
        object(),
        request,
        is_polling=False,
    )

    assert result["task_id"] == "T-child"


@pytest.mark.parametrize(
    "output_dir",
    ("/obs/run", "", "/obs/run/children/part-000"),
)
async def test_dispatch_child_flag_requires_an_exact_directory(
    output_dir: str,
) -> None:
    """Invalid child flags fail before fingerprint reuse can be skipped."""
    request = {
        **_dispatch_request(),
        "output_dir": output_dir,
        "output_dir_is_result_child": True,
    }

    with pytest.raises(ValueError, match="result child"):
        await ada.submit_analyst_via_subgraph(
            fake_submitting_agent("T-invalid-child"),
            SimpleNamespace(USER_ID="user-child"),
            object(),
            request,
            is_polling=False,
        )


async def test_dispatch_context_rejects_an_invalid_flagged_child() -> None:
    """The shared context validates flagged paths for direct callers too."""
    with pytest.raises(ValueError, match="result child"):
        await _analysis_mod.prepare_analyst_dispatch_context(
            SimpleNamespace(USER_ID="user-child"),
            {
                "analysis_type": "design_analysis",
                "target_id": "AT1G01010",
                "output_dir": "/obs/run",
                "output_dir_is_result_child": True,
            },
        )


async def test_standalone_analyst_projects_the_ensured_root_to_first_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone Analyst submits use the first child below their run root."""

    async def ensure_output_dir(*_args: Any, **_kwargs: Any) -> str:
        """Return a deterministic run root through the async seam."""
        return "/obs/run-root"

    monkeypatch.setattr(
        _analyst_graph_mod,
        "ensure_run_output_dir",
        ensure_output_dir,
    )
    agent = SimpleNamespace(
        analyst_config=SimpleNamespace(CREATE_DIR=True),
        sensitive_config=object(),
    )

    output_dir = await getattr(AnalystGraphMixin, "_submit_output_dir")(
        agent,
        {"output_dir": "", "input_fingerprint": "fingerprint"},
        RunIdentity.create(user_id="user-child"),
    )

    assert output_dir == "/obs/run-root/children/part-001"


async def test_standalone_analyst_ignores_shared_default_output_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config test dump is not a caller-allocated run root.

    HTTP/MCP Analyst historically seeds ``AnalystConfig.OUTPUT_DIR``.
    Reusing that prefix makes harvest list leftover objects from every
    prior job that wrote under the shared test output directory.
    """
    captured: dict[str, Any] = {}

    async def ensure_output_dir(*args: Any, **kwargs: Any) -> str:
        captured["output_dir"] = (
            kwargs["output_dir"]
            if "output_dir" in kwargs
            else args[3] if len(args) > 3 else None
        )
        return "/obs/run-scoped"

    monkeypatch.setattr(
        _analyst_graph_mod,
        "ensure_run_output_dir",
        ensure_output_dir,
    )
    default = "/obs/phytomni/agent_data/test/output"
    agent = SimpleNamespace(
        analyst_config=SimpleNamespace(
            CREATE_DIR=True,
            OUTPUT_DIR=default,
        ),
        sensitive_config=object(),
    )

    output_dir = await getattr(AnalystGraphMixin, "_submit_output_dir")(
        agent,
        {"output_dir": default, "input_fingerprint": ""},
        RunIdentity.create(user_id="user-http"),
    )

    assert captured["output_dir"] == ""
    assert output_dir == "/obs/run-scoped/children/part-001"


async def test_standalone_analyst_ignores_default_child_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child under the shared dump is still unallocated unless flagged."""
    captured: dict[str, Any] = {}

    async def ensure_output_dir(*args: Any, **kwargs: Any) -> str:
        captured["output_dir"] = (
            kwargs["output_dir"]
            if "output_dir" in kwargs
            else args[3] if len(args) > 3 else None
        )
        return "/obs/run-scoped"

    monkeypatch.setattr(
        _analyst_graph_mod,
        "ensure_run_output_dir",
        ensure_output_dir,
    )
    default = "/obs/phytomni/agent_data/test/output"
    agent = SimpleNamespace(
        analyst_config=SimpleNamespace(
            CREATE_DIR=True,
            OUTPUT_DIR=default,
        ),
        sensitive_config=object(),
    )

    output_dir = await getattr(AnalystGraphMixin, "_submit_output_dir")(
        agent,
        {
            "output_dir": f"{default}/children/part-001",
            "input_fingerprint": "",
        },
        RunIdentity.create(user_id="user-http"),
    )

    assert captured["output_dir"] == ""
    assert output_dir == "/obs/run-scoped/children/part-001"


async def test_analyst_rejects_an_invalid_flagged_child() -> None:
    """The Analyst node revalidates flagged state before skipping creation."""
    agent = SimpleNamespace(
        analyst_config=SimpleNamespace(CREATE_DIR=True),
        sensitive_config=object(),
    )

    with pytest.raises(ValueError, match="result child"):
        await getattr(AnalystGraphMixin, "_submit_output_dir")(
            agent,
            {
                "output_dir": "/obs/run",
                "output_dir_is_result_child": True,
            },
            RunIdentity.create(user_id="user-child"),
        )
