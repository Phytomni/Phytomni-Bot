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
    build_a2ui_value,
    build_submitted_value,
    mint_surface_id,
    should_emit_confirm,
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
