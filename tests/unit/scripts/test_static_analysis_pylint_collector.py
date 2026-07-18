# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded cross-file Pylint collection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.static_analysis.collectors.errors import CollectionError
from scripts.static_analysis.collectors.pylint import (
    parse_pylint_json,
    validate_pylint_result,
)
from scripts.static_analysis.model import TargetKind

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "tests" / "fixtures" / "static_analysis"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_r0801_uses_message_endpoints_not_json_path() -> None:
    """R0801 paths come from Pylint's endpoint message lines."""
    finding = parse_pylint_json(
        _ROOT, _fixture("pylint-r0801.json"), "pylint 4.0.5"
    )[0]

    assert finding.path == "tests/agents/test_chat_adapters.py"
    assert finding.peer_path == (
        "tests/agents/test_review_follow_up_routing.py"
    )
    assert finding.target_kind is TargetKind.PAIR
    assert finding.tool_version == "pylint 4.0.5"


def test_r0903_resolves_the_class_symbol() -> None:
    """R0903 points to the exact class rather than only its file."""
    finding = parse_pylint_json(
        _ROOT, _fixture("pylint-r0903.json"), "pylint 4.0.5"
    )[0]

    assert finding.path == (
        "src/mcp_server_phytomni/agents/deep_genome/brief_gene_mount.py"
    )
    assert finding.symbol == "RequiredBriefGeneError"
    assert finding.target_kind is TargetKind.SYMBOL


def test_empty_stdout_with_fatal_status_is_an_invocation_error() -> None:
    """Fatal Pylint statuses fail even when stderr contains the detail."""
    with pytest.raises(CollectionError, match="fatal"):
        validate_pylint_result(returncode=1, stdout="", stderr="fatal")


def test_refactor_status_accepts_json_array() -> None:
    """The refactor bit is the only non-zero status allowed here."""
    assert validate_pylint_result(8, "[]", "") == "[]"


@pytest.mark.parametrize("status", [2, 4, 16, 32, 64])
def test_unexpected_status_is_rejected(status: int) -> None:
    """Usage, error, convention, warning, and fatal bits are rejected."""
    with pytest.raises(CollectionError, match="status"):
        validate_pylint_result(status, "[]", "")


def test_unknown_rule_id_is_rejected() -> None:
    """The collector never silently widens its cross-file rule universe."""
    document = json.loads(_fixture("pylint-r0801.json"))
    document[0]["message-id"] = "R9999"
    with pytest.raises(CollectionError, match="unknown Pylint rule"):
        parse_pylint_json(_ROOT, json.dumps(document), "pylint 4.0.5")


def test_truncated_json_is_rejected() -> None:
    """A truncated report cannot be treated as an empty inventory."""
    with pytest.raises(CollectionError, match="malformed"):
        validate_pylint_result(8, "[{", "")


def test_empty_success_output_is_rejected() -> None:
    """A successful command still needs a machine-readable report."""
    with pytest.raises(CollectionError, match="empty JSON"):
        validate_pylint_result(0, "", "")
