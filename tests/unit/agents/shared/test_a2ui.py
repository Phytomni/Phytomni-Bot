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
    A2UI_MAX_ROUNDS,
    A2uiActionEnvelope,
    ConfirmProps,
    FormField,
    FormProps,
    action_to_resume_payload,
    attach_review_a2ui,
    author_a2ui_surface,
    author_a2ui_surface_offline,
    build_a2ui_value,
    build_choice_template_props,
    build_form_template_props,
    build_submitted_value,
    clear_a2ui_for_reenter,
    match_domain_template,
    mint_surface_id,
    next_a2ui_round,
    project_review_confirm,
    review_action_to_resume,
    select_chat_a2ui_widget,
    should_emit_choice,
    should_emit_confirm,
    should_emit_form,
    should_reenter_a2ui,
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
    assert projected["draft"]["a2ui"]["props"]["title"] == "Review approval"
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


def test_review_action_to_resume_maps_accepted() -> None:
    """A2UI confirm becomes Review resume with approved + identity keys."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="confirm",
        action_id="act-1",
        run_id="run-1",
        payload={"accepted": True},
    )
    assert review_action_to_resume(envelope) == {
        "widget": "confirm",
        "surface_id": "sfc-1",
        "action_id": "act-1",
        "edits": None,
        "approved": True,
    }
    envelope_reject = envelope.model_copy(
        update={"payload": {"accepted": False}}
    )
    assert review_action_to_resume(envelope_reject) == {
        "widget": "confirm",
        "surface_id": "sfc-1",
        "action_id": "act-1",
        "edits": None,
        "approved": False,
    }


def test_review_action_to_resume_form_fields() -> None:
    """Form submit maps fields and auto-approves the Review resume."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="form",
        action_id="act-1",
        run_id="run-1",
        payload={"fields": {"gene_id": "AT1G01010"}},
    )
    assert review_action_to_resume(envelope) == {
        "widget": "form",
        "surface_id": "sfc-1",
        "action_id": "act-1",
        "edits": None,
        "fields": {"gene_id": "AT1G01010"},
        "approved": True,
    }


def test_review_action_to_resume_choice_selected() -> None:
    """Choice submit maps selected and auto-approves the Review resume."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="choice",
        action_id="act-1",
        run_id="run-1",
        payload={"selected": ["opt-a"]},
    )
    assert review_action_to_resume(envelope) == {
        "widget": "choice",
        "surface_id": "sfc-1",
        "action_id": "act-1",
        "edits": None,
        "selected": ["opt-a"],
        "approved": True,
    }


def test_review_action_to_resume_cancelled() -> None:
    """Form/choice cancel sets cancelled and approved=false."""
    envelope = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="form",
        action_id="act-1-cancel",
        run_id="run-1",
        payload={"cancelled": True},
    )
    assert review_action_to_resume(envelope) == {
        "widget": "form",
        "surface_id": "sfc-1",
        "action_id": "act-1-cancel",
        "edits": None,
        "cancelled": True,
        "approved": False,
    }


def test_attach_review_a2ui_can_project_form() -> None:
    """Offline author can project a form surface for Review pauses."""
    projected = attach_review_a2ui(
        {"draft": {"draft": "请填写 gene id for the draft"}}
    )
    a2ui = projected["draft"]["a2ui"]
    assert a2ui["widget"] == "form"
    assert a2ui["props"]["title"] == "Gene ID"
    assert a2ui["props"]["fields"][0]["name"] == "gene_id"


def test_select_chat_a2ui_widget_priority() -> None:
    """confirm beats form/choice; form beats choice; else None."""
    assert select_chat_a2ui_widget("请确认并请填写") == "confirm"
    assert select_chat_a2ui_widget("请填写基因名") == "form"
    assert select_chat_a2ui_widget("请选择方案") == "choice"
    assert select_chat_a2ui_widget("What is CRISPR?") is None


def test_should_emit_form_and_choice_shortlist() -> None:
    """Form/choice heuristics hit the approved keyword shortlists."""
    assert should_emit_form("请输入数值") is True
    assert should_emit_form("please enter the id") is True
    assert should_emit_form("hello") is False
    assert should_emit_choice("二选一哪个好") is True
    assert should_emit_choice("choose one option") is True
    assert should_emit_choice("hello") is False


def test_form_and_choice_template_props() -> None:
    """Thin templates match the P4-1d fixed field/option contract."""
    form = build_form_template_props()
    assert form.title == "Form"
    assert len(form.fields) == 1
    assert form.fields[0].name == "value"
    assert form.fields[0].required is True
    choice = build_choice_template_props()
    assert choice.title == "Choice"
    assert choice.multiple is False
    assert [o.id for o in choice.options] == ["a", "b"]


def test_action_to_resume_payload_form_cancelled() -> None:
    """cancelled:true bypasses FormPayload validation."""
    env = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="form",
        action_id="act-1",
        run_id="run-1",
        payload={"cancelled": True},
    )
    payload = action_to_resume_payload(env)
    assert payload["cancelled"] is True
    assert "fields" not in payload


def test_action_to_resume_payload_choice_cancelled() -> None:
    """cancelled:true bypasses ChoicePayload validation."""
    env = A2uiActionEnvelope(
        surface_id="sfc-1",
        widget="choice",
        action_id="act-1",
        run_id="run-1",
        payload={"cancelled": True},
    )
    payload = action_to_resume_payload(env)
    assert payload["cancelled"] is True
    assert "selected" not in payload


def test_build_submitted_value_form_and_choice() -> None:
    """Submitted snapshots echo fields/selected/cancelled."""
    form_prior = build_a2ui_value(
        surface_id="sfc-1",
        widget="form",
        props=build_form_template_props(),
    )
    submitted = build_submitted_value(
        form_prior,
        fields={"value": "AT1G01010"},
    )
    assert submitted["props"]["status"] == "submitted"
    assert submitted["props"]["fields"] == {"value": "AT1G01010"}

    choice_prior = build_a2ui_value(
        surface_id="sfc-1",
        widget="choice",
        props=build_choice_template_props(),
    )
    cancelled = build_submitted_value(choice_prior, cancelled=True)
    assert cancelled["props"]["status"] == "submitted"
    assert cancelled["props"]["cancelled"] is True


def test_match_domain_template_gene_id() -> None:
    """gene_id domain template wins for gene-oriented form queries."""
    hit = match_domain_template("请填写 gene id ATG000")
    assert hit is not None
    assert hit.template_id == "gene_id"
    assert hit.widget == "form"


@pytest.mark.asyncio
async def test_author_prefers_domain_template_over_llm() -> None:
    """Domain hit must not call llm_props_fn."""

    async def _boom(ctx: object, widget: object) -> None:
        del ctx, widget
        raise AssertionError("llm must not run")

    value = await author_a2ui_surface(
        {"text": "请填写 gene_id", "agent": "chat"},
        llm_props_fn=_boom,
    )
    assert value["widget"] == "form"
    assert value["props"]["fields"][0]["name"] == "gene_id"


@pytest.mark.asyncio
async def test_author_llm_then_thin_fallback() -> None:
    """LLM None → thin form template when widget is form."""

    async def _none(ctx: object, widget: object) -> None:
        del ctx, widget
        return None

    value = await author_a2ui_surface(
        {"text": "请填写 something obscure xyz", "agent": "chat"},
        llm_props_fn=_none,
    )
    assert value["widget"] == "form"
    assert value["props"]["fields"][0]["name"] == "value"


def test_author_offline_gene_id_form() -> None:
    """Offline author returns gene_id domain form without LLM."""
    value = author_a2ui_surface_offline(
        {"text": "请填写 gene_id", "agent": "chat"},
    )
    assert value["widget"] == "form"
    assert value["props"]["title"] == "Gene ID"
    assert value["props"]["fields"][0]["name"] == "gene_id"


def test_author_offline_domain_widget_mismatch_uses_thin() -> None:
    """Species domain (choice) is skipped when widget is form."""
    text = "请填写 species for the run"
    assert select_chat_a2ui_widget(text) == "form"
    hit = match_domain_template(text)
    assert hit is not None
    assert hit.template_id == "species"
    assert hit.widget == "choice"
    value = author_a2ui_surface_offline(
        {"text": text, "agent": "chat"},
    )
    assert value["widget"] == "form"
    assert value["props"]["title"] == "Form"
    assert value["props"]["fields"][0]["name"] == "value"


@pytest.mark.asyncio
async def test_author_uses_valid_llm_props() -> None:
    """Valid LLM props are used when no domain template hits."""

    async def _custom(
        ctx: object,
        widget: object,
    ) -> dict[str, object]:
        del ctx, widget
        return {
            "title": "Custom",
            "fields": [
                {
                    "name": "x",
                    "label": "X",
                    "type": "text",
                    "required": True,
                },
            ],
        }

    value = await author_a2ui_surface(
        {"text": "请填写 something obscure xyz", "agent": "chat"},
        llm_props_fn=_custom,
    )
    assert value["widget"] == "form"
    assert value["props"]["title"] == "Custom"
    assert value["props"]["fields"][0]["name"] == "x"


def test_should_reenter_respects_max_rounds() -> None:
    """Re-entry requires a widget cue and a2ui_round below N=2."""
    assert A2UI_MAX_ROUNDS == 2
    assert should_reenter_a2ui(text="请填写 x", a2ui_round=1) is True
    assert should_reenter_a2ui(text="请填写 x", a2ui_round=2) is False
    assert should_reenter_a2ui(text="hello", a2ui_round=0) is False


def test_clear_a2ui_for_reenter_nulls_surface() -> None:
    """clear_a2ui_for_reenter resets surface and decision for remint."""
    cleared = clear_a2ui_for_reenter()
    assert cleared["a2ui_surface"] is None
    assert cleared["a2ui_decision"] is None
    assert next_a2ui_round(None) == 1
    assert next_a2ui_round(1) == 2
