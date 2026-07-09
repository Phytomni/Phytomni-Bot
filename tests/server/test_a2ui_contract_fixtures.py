# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for docs/contracts/a2ui Chat confirm goldens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.a2ui import (
    A2UI_CATALOG_VERSION,
    A2uiActionEnvelope,
    A2uiDownlinkValue,
    build_submitted_value,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_ROOT = _REPO_ROOT / "docs" / "contracts" / "a2ui" / "chat_confirm"
_ERRORS_ROOT = _CONTRACT_ROOT / "errors"

_EXPECTED_ERRORS: dict[str, tuple[int, str]] = {
    "flag_off_403.json": (403, "a2ui disabled"),
    "widget_mismatch_400.json": (400, "widget mismatch"),
    "surface_mismatch_409.json": (409, "surface_id mismatch"),
    "not_input_required_409.json": (409, "run is not awaiting input"),
    "not_owner_404.json": (404, "run not found: run-contract-1"),
    "run_id_mismatch_400.json": (400, "run_id mismatch"),
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_contract_files_exist() -> None:
    """Every golden path from the P4-1c spec is present."""
    required = [
        _CONTRACT_ROOT / "downlink.json",
        _CONTRACT_ROOT / "uplink_accept.json",
        _CONTRACT_ROOT / "uplink_reject.json",
        _CONTRACT_ROOT / "success_accept.json",
        _CONTRACT_ROOT / "success_reject.json",
    ]
    for path in required:
        assert path.is_file(), f"missing golden: {path}"
    for name in _EXPECTED_ERRORS:
        assert (_ERRORS_ROOT / name).is_file(), f"missing error golden: {name}"


def test_downlink_matches_pydantic() -> None:
    """Downlink value validates as A2uiDownlinkValue."""
    raw = _load(_CONTRACT_ROOT / "downlink.json")
    model = A2uiDownlinkValue.model_validate(raw)
    assert model.catalog_version == A2UI_CATALOG_VERSION
    assert model.surface_id == "sfc-contract-1"
    assert model.widget == "confirm"
    assert model.props["title"] == "Continue?"


def test_uplink_accept_and_reject_match_envelope() -> None:
    """Uplink goldens validate as A2uiActionEnvelope."""
    accept = A2uiActionEnvelope.model_validate(
        _load(_CONTRACT_ROOT / "uplink_accept.json")
    )
    assert accept.payload["accepted"] is True
    assert accept.run_id == "run-contract-1"
    assert accept.surface_id == "sfc-contract-1"
    assert accept.action_id == "act-contract-1"

    reject = A2uiActionEnvelope.model_validate(
        _load(_CONTRACT_ROOT / "uplink_reject.json")
    )
    assert reject.payload["accepted"] is False
    assert reject.action_id == "act-contract-1-reject"


def test_success_accept_matches_submitted_shape() -> None:
    """Success accept locks status + submitted a2ui props."""
    downlink = _load(_CONTRACT_ROOT / "downlink.json")
    success = _load(_CONTRACT_ROOT / "success_accept.json")
    assert success["status"] == "succeeded"
    assert "answer" in success["result"]["formatted"]
    expected = build_submitted_value(downlink, accepted=True)
    assert success["result"]["a2ui"] == expected


def test_success_reject_matches_submitted_shape() -> None:
    """Success reject locks accepted=false submitted snapshot."""
    downlink = _load(_CONTRACT_ROOT / "downlink.json")
    success = _load(_CONTRACT_ROOT / "success_reject.json")
    assert success["status"] == "succeeded"
    assert isinstance(success["result"]["formatted"]["answer"], str)
    expected = build_submitted_value(downlink, accepted=False)
    assert success["result"]["a2ui"] == expected


@pytest.mark.parametrize("filename", sorted(_EXPECTED_ERRORS))
def test_error_golden_matches_api_detail(filename: str) -> None:
    """Error goldens mirror api/app.py HTTPException detail strings."""
    status, message = _EXPECTED_ERRORS[filename]
    raw = _load(_ERRORS_ROOT / filename)
    assert raw["status"] == status
    assert raw["error"]["code"] == status
    assert raw["error"]["message"] == message
