# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review exposes finite dimension, citation-batch, and synthesis records."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.review import report as report_module
from mcp_server_phytomni.agents.review.graph_wiring import (
    _review_chat_operation,
)
from mcp_server_phytomni.agents.review.report import ReviewReportMixin


class _CitationProbe(ReviewReportMixin):
    def __init__(self) -> None:
        self.review_config = SimpleNamespace(
            MAX_TOKENS=120,
            PROMPT_FILE="unused.yaml",
        )

    async def _chat(self, user_query: str) -> dict[str, Any]:
        del user_query
        return {"choices": [{"message": {"content": "audited"}}]}


async def test_citation_checks_record_real_batch_ordinals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify citation checks record real batch ordinals."""

    observed: list[tuple[str, Mapping[str, Any]]] = []

    async def instrument(
        operation_key: str,
        call: Callable[[], Awaitable[Any]],
        *,
        detail: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        observed.append((operation_key, detail or {}))
        return await call()

    monkeypatch.setattr(report_module, "get_prompt", lambda *_args: "prompt")
    monkeypatch.setattr(
        report_module,
        "instrument_operation_invocation",
        instrument,
        raising=False,
    )
    result = await getattr(_CitationProbe(), "_audit_citations")(
        content_to_check="Cite [document 001] then [document 002].",
        raw_doc_list=[
            {"doc_id": "document 001", "content": "A" * 200},
            {"doc_id": "document 002", "content": "B" * 200},
        ],
        add_doc_list=[],
    )

    assert result == "audited"
    assert observed == [
        ("review.citation_check", {"ordinal": 1, "total": 2}),
        ("review.citation_check", {"ordinal": 2, "total": 2}),
    ]


def test_review_worker_and_synthesis_seams_declare_finite_operations() -> None:
    """Verify review worker and synthesis seams declare finite operations."""
    root = (
        Path(__file__).parents[2]
        / "src"
        / "mcp_server_phytomni"
        / "agents"
        / "review"
    )
    planning = (root / "planning.py").read_text(encoding="utf-8")
    agent = (root / "agent.py").read_text(encoding="utf-8")
    draft_instrumentation = (root / "draft_instrumentation.py").read_text(
        encoding="utf-8"
    )
    wiring = (root / "graph_wiring.py").read_text(encoding="utf-8")

    assert '"review.retrieve_dimension"' in planning
    assert '"review.draft_dimension"' in draft_instrumentation
    assert '"review.final_synthesis"' in wiring
    assert "dimension_total" in planning
    assert "dimension_total" in agent


def test_review_chat_operation_selects_only_final_synthesis() -> None:
    """Verify review chat operation selects only final synthesis."""

    assert _review_chat_operation(
        {
            "pending_post": "summary_post_node",
            "research_dimensions": ["a", "b", "c"],
        }
    ) == ("review.final_synthesis", {"total": 3})
    assert (
        _review_chat_operation(
            {
                "pending_post": "follow_up_post_node",
                "research_dimensions": ["a", "b", "c"],
            }
        )
        is None
    )
