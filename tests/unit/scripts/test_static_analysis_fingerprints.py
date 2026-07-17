# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for stable source and cross-file finding fingerprints."""

from __future__ import annotations

import pytest
from scripts.static_analysis.fingerprints import (
    Endpoint,
    containing_symbol,
    content_fingerprint,
    finding_fingerprint,
    normalize_source,
    pair_fingerprint,
)

pytestmark = pytest.mark.unit


def test_pair_fingerprint_ignores_order_and_line_shift() -> None:
    """Pair identity is canonical and independent of display line numbers."""
    left = Endpoint("src/a.py", "A.run", "return value")
    right = Endpoint("src/b.py", "B.run", "return value")
    shifted_left = Endpoint("src/a.py", "A.run", "\n\nreturn value")

    assert pair_fingerprint(left=left, right=right) == pair_fingerprint(
        left=right, right=left
    )
    assert pair_fingerprint(left=left, right=right) != pair_fingerprint(
        left=shifted_left, right=right
    )


def test_symbol_locator_chooses_innermost_definition() -> None:
    """Nested functions resolve to their fully qualified innermost name."""
    source = (
        "class A:\n"
        "    def run(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner()\n"
    )

    assert containing_symbol(source, 3) == "A.run.inner"
    assert containing_symbol(source, 1) == "A"
    assert containing_symbol(source, 99) is None


def test_normalization_ignores_comments_and_blank_lines() -> None:
    """Comments and non-significant blank lines do not alter identity."""
    original = "def sample():\n    return 1\n"
    changed = "# explanation\n\n\ndef sample():\n    return 1  # note\n"

    assert normalize_source(original) == normalize_source(changed)
    assert content_fingerprint(
        [normalize_source(original)]
    ) == content_fingerprint([normalize_source(changed)])


def test_normalization_keeps_literals_and_structure() -> None:
    """Literal and operator changes remain visible to the fingerprint."""
    assert normalize_source("value = 1\n") != normalize_source("value = 2\n")
    assert normalize_source("value = 1 + 2\n") != normalize_source(
        "value = 1 - 2\n"
    )
    assert normalize_source('value = "a"\n') != normalize_source(
        'value = "b"\n'
    )


def test_finding_fingerprint_includes_tool_rule_mechanism_and_message() -> (
    None
):
    """Authorization identity changes when any diagnostic identity changes."""
    endpoint = Endpoint("src/a.py", "sample", "return value")
    base = finding_fingerprint(
        "pylint", "R0903", "diagnostic", [endpoint], "too few methods"
    )

    assert base.startswith("sha256:")
    assert base != finding_fingerprint(
        "ruff", "R0903", "diagnostic", [endpoint], "too few methods"
    )
    assert base != finding_fingerprint(
        "pylint", "R0913", "diagnostic", [endpoint], "too few methods"
    )
    assert base != finding_fingerprint(
        "pylint", "R0903", "inline", [endpoint], "too few methods"
    )
    assert base != finding_fingerprint(
        "pylint", "R0903", "diagnostic", [endpoint], "changed message"
    )


def test_content_fingerprint_is_length_delimited() -> None:
    """Concatenation collisions cannot produce the same content identity."""
    assert content_fingerprint(["ab", "c"]) != content_fingerprint(["a", "bc"])
