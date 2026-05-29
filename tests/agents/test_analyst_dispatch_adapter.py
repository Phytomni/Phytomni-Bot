# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst dispatch IO adapters.

Pins ``map_send_payload_to_analyst_input`` against the kwargs
``submit_analyst_analysis`` already forwards to ``AnalystAgent.arun``
and ``map_analyst_output_to_dispatch_state`` against the dict shape
``capture_analysis_result`` reads through ``task_result.get(...)``.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.analyst.state import AnalystInput
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    map_analyst_output_to_dispatch_state,
    map_send_payload_to_analyst_input,
)

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
    expected_optionals = optional_keys - {"obs_file_list"}
    assert set(result.keys()) == expected_optionals | {"query"}


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


def test_map_send_payload_pins_dispatch_flag_constants() -> None:
    """Three boolean flags match what ``submit_analyst_analysis`` pins.

    ``is_polling=False`` (callers poll via ``task_id`` lookups, not
    inside the dispatched arun), ``is_auto_select=False`` (the data
    list is preset), and ``is_preset_plan=True`` (the preset plan
    bypasses the retrieval + critic path).
    """
    payload = _sample_payload()
    result = dict(map_send_payload_to_analyst_input(payload))
    assert result["is_polling"] is False
    assert result["is_auto_select"] is False
    assert result["is_preset_plan"] is True


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
