# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic cleanup of assembled Review manuscript text.

Strips retrieval-meta sentences, planner-string pastes, empty
subheadings, and overclaim title verbs when the body already states a
gap. Citation tags are left untouched.
"""

from __future__ import annotations

import re

__all__ = ["scrub_review_manuscript"]

_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$")
_TITLE_LINE = re.compile(r"(?m)^(#{1,6}[ \t]+Title:[ \t]*)(.+)$")
_OVERCLAIM = re.compile(r"\b(Confers|Establishes|Proves|Demonstrates)\b")
_GAP = re.compile(
    r"lacks direct evidence|remains (?:untested|absent)|"
    r"cannot be substantiated|direct evidence.{0,40}absent",
    re.I,
)
_META = re.compile(
    r"(?is)[^.!?\n]*\b(?:"
    r"supplied knowledge snippets?|"
    r"provided knowledge snippets?|"
    r"none of the supplied|"
    r"cannot be substantiated from the provided|"
    r"from the provided data|"
    r"as referenced in the subtopic"
    r")\b[^.!?\n]*[.!?]?"
)


def scrub_review_manuscript(
    text: str,
    *,
    thesis: str = "",
    in_scope: str = "",
    out_of_scope: str = "",
) -> str:
    """Return assembled Review text with planner and retrieval artifacts gone.

    Args:
        text: Markdown manuscript from the summary model.
        thesis: Planner thesis; used only as a verbatim-paste needle.
        in_scope: Planner in-scope sentence to remove if pasted.
        out_of_scope: Planner out-of-scope sentence to remove if pasted.

    Returns:
        Cleaned manuscript. Citation tags are preserved.
    """
    cleaned = _rewrite_snippet_talk(text)
    cleaned = _META.sub("", cleaned)
    for blob in (thesis, in_scope, out_of_scope):
        cleaned = _drop_verbatim_blob(cleaned, blob)
    cleaned = _repair_here_we_review(cleaned)
    cleaned = _soften_overclaim_title(cleaned)
    cleaned = _drop_orphan_subheadings(cleaned)
    return _collapse_blank_lines(cleaned)


def _drop_verbatim_blob(text: str, blob: str) -> str:
    """Remove one planner sentence wherever it was pasted wholesale."""
    words = blob.strip().rstrip(".").split()
    if len(words) < 5:
        return text
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.I)
    stripped = pattern.sub("", text)
    stripped = re.sub(r"(?i)\s*are examined herein\.?", "", stripped)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r"(?m)[ \t]+\n", "\n", stripped)
    return stripped


def _repair_here_we_review(text: str) -> str:
    """Repair holes left after deleting a pasted scope clause."""
    repaired = re.sub(r"Here we review\s+([A-Z])", r"Here we review \1", text)
    repaired = re.sub(
        r"Here we review(?:\s+\w+){0,2}(?:\s*\.)+",
        "Here we review this topic.",
        repaired,
    )
    repaired = re.sub(
        r"Here we review\s*(?=\n#|$)",
        "Here we review this topic.",
        repaired,
    )
    return re.sub(r"\.\s+\.", ".", repaired)


_SNIPPET_TALK = (
    (
        re.compile(r"(?i)\bthe supplied knowledge demonstrates that\s+"),
        "",
    ),
    (
        re.compile(r"(?i)\bnot provided in these snippets\b"),
        "not reported",
    ),
    (
        re.compile(r"(?i)\bin the supplied documents\b"),
        "in current evidence",
    ),
    (re.compile(r"(?i)\bthese snippets\b"), "current evidence"),
)


def _rewrite_snippet_talk(text: str) -> str:
    """Replace leftover retrieval-process phrasing with scientific wording."""
    rewritten = text
    for pattern, replacement in _SNIPPET_TALK:
        rewritten = pattern.sub(replacement, rewritten)
    return rewritten


def _soften_overclaim_title(text: str) -> str:
    """Replace confers-style title verbs when the body reports a gap."""
    if not _GAP.search(text):
        return text
    match = _TITLE_LINE.search(text)
    if match is None:
        return text
    title = match.group(2)
    if _OVERCLAIM.search(title) is None:
        return text
    softened = _OVERCLAIM.sub("Is Proposed for", title, count=1)
    start = match.start(2)
    end = match.end(2)
    return text[:start] + softened + text[end:]


def _drop_orphan_subheadings(text: str) -> str:
    """Drop a #### heading that has no remaining body."""
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line.rstrip("\n"))
        if heading is None or len(heading.group(1)) < 4:
            kept.append(line)
            index += 1
            continue
        lookahead = index + 1
        body_found = False
        while lookahead < len(lines):
            nxt = lines[lookahead]
            if _HEADING.match(nxt.rstrip("\n")):
                break
            if nxt.strip():
                body_found = True
                break
            lookahead += 1
        if body_found:
            kept.append(line)
        index += 1
    return "".join(kept)


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of blank lines left by sentence deletion."""
    collapsed = re.sub(r"\n{3,}", "\n\n", text)
    return collapsed.strip() + ("\n" if text.endswith("\n") else "")
