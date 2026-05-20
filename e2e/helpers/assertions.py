# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared assertion constants and markdown helpers for e2e tests.

Centralizes the keyword/cue tuples and the citation-envelope markdown
unwrap + section counter so the MCP and HTTP e2e suites assert against
one source instead of cross-file duplicated literals.
"""

from __future__ import annotations

import json
import re

PHOTOSYNTHESIS_KEYWORDS = ("photosynthesis", "c3", "calvin", "rubisco")
WHEAT_DROUGHT_KEYWORDS = (
    "drought",
    "wheat",
    "triticum",
    "aba",
    "dreb",
    "snrk",
)
GENE_ID = "Os01g0177400"
ANNOTATION_CUES = (
    "function",
    "expression",
    "ortholog",
    "homolog",
    "domain",
    "pathway",
    "literature",
    "tissue",
)
MIN_REVIEW_SECTIONS = 3

# A markdown section header: an ATX heading of depth >= 2 (## .. ######).
# Depth >= 2 (not a literal "## ") because the drafting LLM varies the
# level it uses for sections run-to-run (## vs ###) while reserving a
# single # / ## for the document title; counting any sub-title heading
# captures "the multi-section path ran" without being brittle to that
# variation, yet still yields 0 for an empty or unstructured answer.
ATX_SECTION = re.compile(r"^\s{0,3}#{2,6}\s+\S")


def markdown_body(answer: str) -> str:
    """Return the markdown body from a possibly JSON-wrapped answer.

    Citation-bearing agents serialize ``answer`` as a JSON envelope
    ``{"content": "<markdown>", "doc_list": [...]}``; unwrap that when
    present, otherwise return the raw string (plain-markdown path).

    Args:
        answer: Raw answer / message-content string.

    Returns:
        The markdown body to scan for section headers.
    """
    try:
        parsed = json.loads(answer)
    except (ValueError, TypeError):
        return answer
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        return parsed["content"]
    return answer


def section_count(answer: str) -> int:
    """Count depth>=2 ATX markdown headers in a possibly wrapped answer.

    Unwraps the citation envelope via ``markdown_body`` then counts
    depth>=2 ATX headings (see ``ATX_SECTION``).

    Args:
        answer: Raw answer / message-content string.

    Returns:
        Number of depth>=2 ATX section headers found.
    """
    body = markdown_body(answer)
    return sum(1 for line in body.splitlines() if ATX_SECTION.match(line))
