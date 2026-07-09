# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for shared A2UI helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.shared.a2ui import (
    A2UI_CATALOG_VERSION,
    A2uiActionEnvelope,
    ConfirmProps,
    FormField,
    FormProps,
    action_to_resume_payload,
    attach_review_a2ui,
    build_a2ui_value,
    build_submitted_value,
    mint_surface_id,
    project_review_confirm,
    review_confirm_action_to_resume,
    should_emit_confirm,
    summary_text_from_interrupt_draft,
)

pytestmark = pytest.mark.unit


def test_build_confirm_value_shape() -> None:
    """Downlink confirm value matches the Web contract."""
    surface = mint_surface_id()
    value = build_a2ui_value(
        surface_id=surface,
        widget="confirm",
        props=ConfirmProps(title="Continue?", body="Run analysis"),
    )
    assert value["catalog_version"] == A2UI_CATALOG_VERSION
    assert value["surface_id"] == surface
    assert value["widget"] == "confirm"
    assert value["props"]["title"] == "Continue?"


def test_build_form_field_dump_uses_type_key() -> None:
    """Form field downlink serializes field type under the public type key."""
    surface = mint_surface_id()
    value = build_a2ui_value(
        surface_id=surface,
        widget="form",
        props=FormProps(
            title="Details",
            fields=[
                FormField(name="gene", label="Gene"),
            ],
        ),
    )
    field = value["props"]["fields"][0]
    assert "type" in field
    assert field["type"] == "text"
    assert "field_type" not in field


def test_form_field_validates_type_alias() -> None:
    """FormField accepts the Web type key on validation."""
    field = FormField.model_validate(
        {
            "name": "gene",
            "label": "Gene",
            "type": "select",
        }
    )
    assert field.field_type == "select"


def test_should_emit_confirm_matches_shortlist() -> None:
    """Confirm heuristic hits explicit ask-to-confirm queries only."""
    assert should_emit_confirm("请确认是否继续分析") is True
    assert should_emit_confirm("Confirm before proceeding?") is True
    assert should_emit_confirm("What is CRISPR?") is False


def test_action_to_resume_payload_confirm() -> None:
    """Confirm action translates into a resume dict with accepted."""
    env = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="confirm",
        action_id="act-1",
        run_id="run-1",
        payload={"accepted": True},
    )
    payload = action_to_resume_payload(env)
    assert payload["accepted"] is True
    assert payload["surface_id"] == "sfc-1"
    assert payload["widget"] == "confirm"


@pytest.mark.parametrize(
    "bad_payload",
    [
        {},
        {"accepted": []},
    ],
)
def test_action_to_resume_payload_confirm_invalid_raises(
    bad_payload: dict[str, object],
) -> None:
    """Invalid confirm payload surfaces as ValueError, not raw Pydantic."""
    env = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="confirm",
        action_id="act-1",
        run_id="run-1",
        payload=bad_payload,
    )
    with pytest.raises(
        ValueError, match="Invalid confirm action payload"
    ) as exc:
        action_to_resume_payload(env)
    assert exc.value.args[0] == "Invalid confirm action payload"


def test_form_and_choice_envelopes_validate() -> None:
    """Form and choice action envelopes parse for infrastructure tests."""
    form = A2uiActionEnvelope(
        surface_id="s",
        widget="form",
        action_id="a",
        run_id="r",
        payload={"fields": {"n": 1, "name": "x"}},
    )
    choice = A2uiActionEnvelope(
        surface_id="s",
        widget="choice",
        action_id="a",
        run_id="r",
        payload={"selected": ["o1"]},
    )
    assert "fields" in action_to_resume_payload(form)
    assert action_to_resume_payload(choice)["selected"] == ["o1"]


def test_build_submitted_value_marks_status() -> None:
    """Submitted downlink keeps surface_id and sets status=submitted."""
    prior = build_a2ui_value(
        surface_id="sfc-1",
        widget="confirm",
        props=ConfirmProps(title="Continue?"),
    )
    submitted = build_submitted_value(prior, accepted=True)
    assert submitted["surface_id"] == "sfc-1"
    assert submitted["props"]["status"] == "submitted"
    assert submitted["props"]["accepted"] is True


def test_invalid_confirm_props_raise() -> None:
    """Missing title fails ConfirmProps validation."""
    with pytest.raises(ValidationError):
        ConfirmProps.model_validate({})


def test_summary_text_from_interrupt_draft_prefers_draft_key() -> None:
    """Production approval_node value uses nested draft text."""
    assert (
        summary_text_from_interrupt_draft({"draft": "Full summary text"})
        == "Full summary text"
    )


def test_summary_text_from_interrupt_draft_accepts_plain_string() -> None:
    """String drafts pass through unchanged."""
    assert summary_text_from_interrupt_draft("plain") == "plain"


def test_summary_text_from_interrupt_draft_uses_summary_key() -> None:
    """Mapping drafts may expose summary instead of draft."""
    assert (
        summary_text_from_interrupt_draft({"summary": "Summary line"})
        == "Summary line"
    )


def test_summary_text_from_interrupt_draft_stringifies_other_values() -> None:
    """Non-string draft values stringify for display."""
    assert summary_text_from_interrupt_draft(42) == "42"


def test_project_review_confirm_shape_and_truncation() -> None:
    """Review confirm downlink truncates body and mints a surface id."""
    long_summary = "x" * 600
    value = project_review_confirm(long_summary)
    assert value["catalog_version"] == A2UI_CATALOG_VERSION
    assert value["widget"] == "confirm"
    assert value["props"]["title"] == "Review approval"
    assert value["props"]["body"] == "x" * 500
    assert isinstance(value["surface_id"], str)
    assert value["surface_id"]


def test_attach_review_a2ui_adds_surface_without_mutating_input() -> None:
    """Registry projection nests a2ui beside the text draft."""
    original = {
        "thread_id": "run-1",
        "draft": {"draft": "Summary for humans"},
    }
    projected = attach_review_a2ui(original)
    assert "a2ui" not in original["draft"]
    assert projected["draft"]["draft"] == "Summary for humans"
    assert projected["draft"]["a2ui"]["widget"] == "confirm"
    assert projected["draft"]["a2ui"]["props"]["body"] == "Summary for humans"


def test_attach_review_a2ui_wraps_non_mapping_draft() -> None:
    """Non-mapping draft values become a nested draft dict plus a2ui."""
    projected = attach_review_a2ui({"draft": "Bare summary"})
    draft_value = projected.get("draft")
    assert isinstance(draft_value, dict)
    assert draft_value.get("draft") == "Bare summary"
    assert draft_value.get("a2ui", {}).get("widget") == "confirm"


def test_attach_review_a2ui_mints_new_surface_each_call() -> None:
    """Each pause round gets a fresh surface_id."""
    interrupt = {
        "thread_id": "run-1",
        "draft": {"draft": "Same text"},
    }
    first = attach_review_a2ui(interrupt)
    second = attach_review_a2ui(interrupt)
    assert (
        first["draft"]["a2ui"]["surface_id"]
        != second["draft"]["a2ui"]["surface_id"]
    )


def test_review_confirm_action_to_resume_maps_accepted() -> None:
    """A2UI confirm becomes Review ResumePayload with edits null."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="confirm",
        action_id="act-1",
        run_id="run-1",
        payload={"accepted": True},
    )
    assert review_confirm_action_to_resume(envelope) == {
        "approved": True,
        "edits": None,
    }
    envelope_reject = envelope.model_copy(
        update={"payload": {"accepted": False}}
    )
    assert review_confirm_action_to_resume(envelope_reject) == {
        "approved": False,
        "edits": None,
    }


def test_review_confirm_action_rejects_non_confirm_widget() -> None:
    """Review A2UI path only accepts confirm widgets."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="form",
        action_id="act-1",
        run_id="run-1",
        payload={"fields": {"x": 1}},
    )
    with pytest.raises(ValueError, match="confirm"):
        review_confirm_action_to_resume(envelope)
