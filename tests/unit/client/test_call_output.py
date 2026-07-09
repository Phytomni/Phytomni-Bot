# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for phytomni call stdout rendering."""

from __future__ import annotations

import pytest

from mcp_client_phytomni.call_output import render_call_output
from mcp_client_phytomni.tool_result_formatters import FormattedToolResult

pytestmark = pytest.mark.unit


def test_render_answer_only() -> None:
    """Answer-only results print as a bare string."""
    text = render_call_output(FormattedToolResult(answer="Hello"))
    assert text == "Hello"


def test_render_includes_tabular_tsv() -> None:
    """DataAgent tabular blocks append as TSV after the answer."""
    text = render_call_output(
        FormattedToolResult(
            answer="1 row x 2 columns",
            tabular={
                "headers": ["alias", "gene"],
                "rows": [["ACT2", "AT3G18780"]],
            },
        )
    )
    assert text.startswith("1 row x 2 columns\n---\n")
    assert "alias\tgene" in text
    assert "ACT2\tAT3G18780" in text


def test_render_includes_references() -> None:
    """Cited-tool references append as numbered title lines."""
    text = render_call_output(
        FormattedToolResult(
            answer="Body [1]",
            references=({"ti": "Paper One", "file_id": "f1"},),
        )
    )
    assert "[1] Paper One" in text


def test_render_includes_task_metadata_line() -> None:
    """Async submit metadata collapses to one key=value summary line."""
    text = render_call_output(
        FormattedToolResult(
            answer="Task created successfully:t1",
            metadata={
                "task_id": "t1",
                "output_dir": "/obs/out",
                "status": "RUNNING",
                "failures": (),
            },
        )
    )
    assert "task_id=t1" in text
    assert "output_dir=/obs/out" in text
    assert "status=RUNNING" in text
