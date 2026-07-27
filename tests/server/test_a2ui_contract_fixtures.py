# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for docs/contracts/a2ui contract goldens."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.support.a2ui_contract_fakes import gene_id_form_props

from mcp_server_phytomni.agents.shared.a2ui import (
    A2UI_CATALOG_VERSION,
    A2uiActionEnvelope,
    A2uiDownlinkValue,
    build_choice_template_props,
    build_form_template_props,
    build_submitted_value,
    validate_a2ui_surface,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_A2UI_ROOT = _REPO_ROOT / "docs" / "contracts" / "a2ui"
_HTTP_ROOT = _REPO_ROOT / "docs" / "contracts" / "http"

_EXPECTED_HTTP_GOLDENS = frozenset(
    {
        "chat_terminal_succeeded.json",
        "review_terminal_succeeded.json",
        "review_round2_input_required.json",
        "error_400_run_widget_payload_mismatch.json",
        "error_403_action_flag_off.json",
        "error_404_owner_safe_not_found.json",
        "error_409_already_handled.json",
        "error_422_capability_validation.json",
        "a2ui_request_64k_exact.json",
        "a2ui_request_64k_plus_one.json",
        "a2ui_response_1m_exact.json",
        "a2ui_response_1m_plus_one.json",
        "streamed_run_accumulated_answer.json",
        "deep_genome_bounded_reports.json",
        "remote_partial_acceptance.json",
        "remote_registry_degraded.json",
    }
)

_FORBIDDEN_HTTP_GOLDEN_TEXT = (
    "Bearer ",
    "/home/",
    "SELECT ",
    "PROVIDER-PAYLOAD",
)

_EXPECTED_ERRORS: dict[str, tuple[int, str]] = {
    "flag_off_403.json": (403, "a2ui disabled"),
    "widget_mismatch_400.json": (400, "widget mismatch"),
    "surface_mismatch_409.json": (409, "surface_id mismatch"),
    "not_input_required_409.json": (409, "run is not awaiting input"),
    "not_owner_404.json": (404, "run not found: run-contract-1"),
    "run_id_mismatch_400.json": (400, "run_id mismatch"),
}


@dataclass(frozen=True)
class _ConfirmContract:
    name: str
    title: str
    body: str
    has_errors: bool


@dataclass(frozen=True)
class _FormChoiceContract:
    name: str
    widget: str
    submit_payload: dict[str, Any]
    cancel_action_id: str
    success_answers: tuple[str, str]
    submitted_submit: Callable[[dict[str, Any]], dict[str, Any]]
    submitted_cancel: Callable[[dict[str, Any]], dict[str, Any]]


_CONFIRM_CONTRACTS = (
    _ConfirmContract(
        name="chat_confirm",
        title="Continue?",
        body="Run the analysis as planned.",
        has_errors=True,
    ),
    _ConfirmContract(
        name="review_confirm",
        title="Review approval",
        body="Draft summary for approval.",
        has_errors=True,
    ),
)

_GENE_ID_PROPS = gene_id_form_props()

_YES_NO_PROPS = {
    "title": "Yes / No",
    "options": [
        {"id": "yes", "label": "Yes"},
        {"id": "no", "label": "No"},
    ],
    "multiple": False,
}

_RICH_DOWNLINK_PROPS: dict[str, dict[str, Any]] = {
    "chat_form": _GENE_ID_PROPS,
    "review_form": _GENE_ID_PROPS,
    "review_choice": _YES_NO_PROPS,
}

_FORM_CHOICE_CONTRACTS = (
    _FormChoiceContract(
        name="chat_form",
        widget="form",
        submit_payload={"fields": {"gene_id": "AT1G01010"}},
        cancel_action_id="act-contract-1-cancel",
        success_answers=("Form submitted.", "Form cancelled."),
        submitted_submit=lambda downlink: build_submitted_value(
            downlink, fields={"gene_id": "AT1G01010"}
        ),
        submitted_cancel=lambda downlink: build_submitted_value(
            downlink, cancelled=True
        ),
    ),
    _FormChoiceContract(
        name="chat_choice",
        widget="choice",
        submit_payload={"selected": "a"},
        cancel_action_id="act-contract-1-cancel",
        success_answers=("Choice submitted.", "Choice cancelled."),
        submitted_submit=lambda downlink: build_submitted_value(
            downlink, selected="a"
        ),
        submitted_cancel=lambda downlink: build_submitted_value(
            downlink, cancelled=True
        ),
    ),
    _FormChoiceContract(
        name="review_form",
        widget="form",
        submit_payload={"fields": {"gene_id": "AT1G01010"}},
        cancel_action_id="act-contract-1-cancel",
        success_answers=(
            "Review form submitted.",
            "Review form cancelled.",
        ),
        submitted_submit=lambda downlink: build_submitted_value(
            downlink, fields={"gene_id": "AT1G01010"}
        ),
        submitted_cancel=lambda downlink: build_submitted_value(
            downlink, cancelled=True
        ),
    ),
    _FormChoiceContract(
        name="review_choice",
        widget="choice",
        submit_payload={"selected": "yes"},
        cancel_action_id="act-contract-1-cancel",
        success_answers=(
            "Review choice submitted.",
            "Review choice cancelled.",
        ),
        submitted_submit=lambda downlink: build_submitted_value(
            downlink, selected="yes"
        ),
        submitted_cancel=lambda downlink: build_submitted_value(
            downlink, cancelled=True
        ),
    ),
)

_INPUT_REQUIRED_CONTRACTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "review_confirm",
        "input_required.json",
        "surface-review-fixture",
        "confirm",
    ),
    (
        "review_form",
        "input_required.json",
        "surface-review-form-fixture",
        "form",
    ),
    (
        "review_choice",
        "input_required.json",
        "surface-review-choice-fixture",
        "choice",
    ),
    ("multi_turn", "round2_input_required.json", "sfc-contract-2", "choice"),
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_root(name: str) -> Path:
    return _A2UI_ROOT / name


@pytest.mark.parametrize("contract", _CONFIRM_CONTRACTS, ids=lambda c: c.name)
def test_confirm_contract_files_exist(contract: _ConfirmContract) -> None:
    """Every confirm golden path from the P4-1c/1d spec is present."""
    root = _contract_root(contract.name)
    required = [
        root / "downlink.json",
        root / "uplink_accept.json",
        root / "uplink_reject.json",
        root / "success_accept.json",
        root / "success_reject.json",
    ]
    for path in required:
        assert path.is_file(), f"missing golden: {path}"
    if contract.has_errors:
        for name in _EXPECTED_ERRORS:
            assert (
                root / "errors" / name
            ).is_file(), f"missing error golden: {name}"


@pytest.mark.parametrize(
    "contract", _FORM_CHOICE_CONTRACTS, ids=lambda c: c.name
)
def test_form_choice_contract_files_exist(
    contract: _FormChoiceContract,
) -> None:
    """Every form/choice golden path from the P4-1d spec is present."""
    root = _contract_root(contract.name)
    required = [
        root / "downlink.json",
        root / "uplink_submit.json",
        root / "uplink_cancel.json",
        root / "success_submit.json",
        root / "success_cancel.json",
    ]
    for path in required:
        assert path.is_file(), f"missing golden: {path}"


@pytest.mark.parametrize("contract", _CONFIRM_CONTRACTS, ids=lambda c: c.name)
def test_confirm_downlink_matches_pydantic(contract: _ConfirmContract) -> None:
    """Confirm downlink value validates as A2uiDownlinkValue."""
    root = _contract_root(contract.name)
    raw = _load(root / "downlink.json")
    model = A2uiDownlinkValue.model_validate(raw)
    assert model.catalog_version == A2UI_CATALOG_VERSION
    assert model.surface_id == "sfc-contract-1"
    assert model.widget == "confirm"
    assert model.props["title"] == contract.title
    assert model.props["body"] == contract.body


@pytest.mark.parametrize(
    "contract", _FORM_CHOICE_CONTRACTS, ids=lambda c: c.name
)
def test_form_choice_downlink_matches_pydantic(
    contract: _FormChoiceContract,
) -> None:
    """Form/choice downlink props match the fixed template builders."""
    root = _contract_root(contract.name)
    raw = _load(root / "downlink.json")
    model = A2uiDownlinkValue.model_validate(raw)
    assert model.catalog_version == A2UI_CATALOG_VERSION
    assert model.surface_id == "sfc-contract-1"
    assert model.widget == contract.widget
    if contract.name in _RICH_DOWNLINK_PROPS:
        expected = _RICH_DOWNLINK_PROPS[contract.name]
    elif contract.widget == "form":
        expected = build_form_template_props().model_dump(
            exclude_none=True, by_alias=True
        )
    else:
        expected = build_choice_template_props().model_dump(
            exclude_none=True, by_alias=True
        )
    assert model.props == expected


@pytest.mark.parametrize("contract", _CONFIRM_CONTRACTS, ids=lambda c: c.name)
def test_confirm_uplink_accept_and_reject_match_envelope(
    contract: _ConfirmContract,
) -> None:
    """Confirm uplink goldens validate as A2uiActionEnvelope."""
    root = _contract_root(contract.name)
    accept = A2uiActionEnvelope.model_validate(
        _load(root / "uplink_accept.json")
    )
    assert accept.payload["accepted"] is True
    assert accept.run_id == "run-contract-1"
    assert accept.surface_id == "sfc-contract-1"
    assert accept.action_id == "act-contract-1"
    assert accept.widget == "confirm"

    reject = A2uiActionEnvelope.model_validate(
        _load(root / "uplink_reject.json")
    )
    assert reject.payload["accepted"] is False
    assert reject.action_id == "act-contract-1-reject"
    assert reject.widget == "confirm"


@pytest.mark.parametrize(
    "contract", _FORM_CHOICE_CONTRACTS, ids=lambda c: c.name
)
def test_form_choice_uplink_submit_and_cancel_match_envelope(
    contract: _FormChoiceContract,
) -> None:
    """Form/choice uplink goldens validate as A2uiActionEnvelope."""
    root = _contract_root(contract.name)
    submit = A2uiActionEnvelope.model_validate(
        _load(root / "uplink_submit.json")
    )
    assert submit.payload == contract.submit_payload
    assert submit.run_id == "run-contract-1"
    assert submit.surface_id == "sfc-contract-1"
    assert submit.action_id == "act-contract-1"
    assert submit.widget == contract.widget

    cancel = A2uiActionEnvelope.model_validate(
        _load(root / "uplink_cancel.json")
    )
    assert cancel.payload == {"cancelled": True}
    assert cancel.action_id == contract.cancel_action_id
    assert cancel.widget == contract.widget


@pytest.mark.parametrize("contract", _CONFIRM_CONTRACTS, ids=lambda c: c.name)
def test_confirm_success_accept_matches_submitted_shape(
    contract: _ConfirmContract,
) -> None:
    """Confirm success accept locks status + submitted a2ui props."""
    root = _contract_root(contract.name)
    downlink = _load(root / "downlink.json")
    success = _load(root / "success_accept.json")
    assert success["status"] == "succeeded"
    assert "answer" in success["result"]["formatted"]
    expected = build_submitted_value(downlink, accepted=True)
    assert success["result"]["a2ui"] == expected


@pytest.mark.parametrize("contract", _CONFIRM_CONTRACTS, ids=lambda c: c.name)
def test_confirm_success_reject_matches_submitted_shape(
    contract: _ConfirmContract,
) -> None:
    """Confirm success reject locks accepted=false submitted snapshot."""
    root = _contract_root(contract.name)
    downlink = _load(root / "downlink.json")
    success = _load(root / "success_reject.json")
    assert success["status"] == "succeeded"
    assert isinstance(success["result"]["formatted"]["answer"], str)
    expected = build_submitted_value(downlink, accepted=False)
    assert success["result"]["a2ui"] == expected


@pytest.mark.parametrize(
    "contract", _FORM_CHOICE_CONTRACTS, ids=lambda c: c.name
)
def test_form_choice_success_submit_matches_submitted_shape(
    contract: _FormChoiceContract,
) -> None:
    """Form/choice success submit locks status + submitted a2ui props."""
    root = _contract_root(contract.name)
    downlink = _load(root / "downlink.json")
    success = _load(root / "success_submit.json")
    assert success["status"] == "succeeded"
    assert (
        success["result"]["formatted"]["answer"] == contract.success_answers[0]
    )
    expected = contract.submitted_submit(downlink)
    assert success["result"]["a2ui"] == expected


@pytest.mark.parametrize(
    "contract", _FORM_CHOICE_CONTRACTS, ids=lambda c: c.name
)
def test_form_choice_success_cancel_matches_submitted_shape(
    contract: _FormChoiceContract,
) -> None:
    """Form/choice success cancel locks cancelled=true submitted snapshot."""
    root = _contract_root(contract.name)
    downlink = _load(root / "downlink.json")
    success = _load(root / "success_cancel.json")
    assert success["status"] == "succeeded"
    assert (
        success["result"]["formatted"]["answer"] == contract.success_answers[1]
    )
    expected = contract.submitted_cancel(downlink)
    assert success["result"]["a2ui"] == expected


@pytest.mark.parametrize(
    ("contract_name", "filename"),
    [
        (contract.name, filename)
        for contract in _CONFIRM_CONTRACTS
        if contract.has_errors
        for filename in sorted(_EXPECTED_ERRORS)
    ],
    ids=lambda value: (
        f"{value[0]}/{value[1]}" if isinstance(value, tuple) else str(value)
    ),
)
def test_error_golden_matches_api_detail(
    contract_name: str,
    filename: str,
) -> None:
    """Error goldens mirror api/app.py HTTPException detail strings."""
    status, message = _EXPECTED_ERRORS[filename]
    raw = _load(_contract_root(contract_name) / "errors" / filename)
    assert raw["status"] == status
    assert raw["error"]["code"] == status
    assert raw["error"]["message"] == message


def test_multi_turn_round2_downlink_exists() -> None:
    """Round-2 multi-turn golden uses sfc-contract-2 choice shape."""
    path = _A2UI_ROOT / "multi_turn" / "round2_downlink.json"
    assert path.is_file(), f"missing golden: {path}"
    raw = _load(path)
    model = A2uiDownlinkValue.model_validate(raw)
    assert model.catalog_version == A2UI_CATALOG_VERSION
    assert model.surface_id == "sfc-contract-2"
    assert model.widget == "choice"


@pytest.mark.parametrize(
    ("name", "filename", "surface_id", "widget"),
    _INPUT_REQUIRED_CONTRACTS,
    ids=lambda value: value[0] if isinstance(value, tuple) else str(value),
)
def test_input_required_golden_is_full_review_body(
    name: str,
    filename: str,
    surface_id: str,
    widget: str,
) -> None:
    """Full Review pause bodies carry a strict, resumable surface."""
    raw = _load(_A2UI_ROOT / name / filename)
    assert raw["id"] == raw["run_id"]
    assert raw["object"] == "agent.run"
    assert raw["agent"] == "review"
    assert raw["status"] == "input_required"
    assert raw["task_ids"] == []
    interrupt = raw["result"]["interrupt"]
    assert interrupt["thread_id"] == raw["run_id"]
    surface = interrupt["draft"]["a2ui"]
    validated = A2uiDownlinkValue.model_validate(surface)
    validate_a2ui_surface(surface)
    assert validated.surface_id == surface_id
    assert validated.widget == widget


def test_multi_turn_input_required_uses_fresh_surface_id() -> None:
    """Round two cannot replay the round-one Review surface id."""
    round_one = _load(_A2UI_ROOT / "review_confirm" / "input_required.json")
    round_two = _load(_A2UI_ROOT / "multi_turn" / "round2_input_required.json")
    first_id = round_one["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]
    second_id = round_two["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]
    assert first_id != second_id


def test_http_golden_set_is_complete_and_redacted() -> None:
    """HTTP evidence goldens are complete JSON bodies without secrets."""
    files = {item.name for item in _HTTP_ROOT.glob("*.json")}
    assert files == _EXPECTED_HTTP_GOLDENS
    for path in sorted(_HTTP_ROOT.glob("*.json")):
        payload = _load(path)
        assert isinstance(payload, dict)
        assert payload["contract"] == path.stem
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in _FORBIDDEN_HTTP_GOLDEN_TEXT:
            assert forbidden not in serialized
