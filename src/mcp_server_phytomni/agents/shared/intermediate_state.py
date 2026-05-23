# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Merge LangGraph intermediate state into the final-response payload.

Agents that return `final_state["final_response"]` discard every
intermediate state field they computed (retrieved_docs, rewrite_query,
research_dimensions, ...). This helper lifts those fields into one
nested `phytomni_state` block so the HTTP/MCP `raw` envelope exposes
them without each agent inlining the merge.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = ["merge_intermediate_state"]


def merge_intermediate_state(
    final_state: Mapping[str, Any],
    final_response_key: str = "final_response",
    extra_excluded_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Return ``final_response`` enriched with a ``phytomni_state`` block.

    Every key in ``final_state`` other than ``final_response_key`` (and
    any key in ``extra_excluded_keys`` or starting with ``_``) is
    surfaced under one stable ``phytomni_state`` namespace so a frontend
    can opt into the LangGraph intermediate fields without scraping the
    OpenAI canonical top level.

    Args:
        final_state: LangGraph compiled graph output.
        final_response_key: State key whose value becomes the base dict.
        extra_excluded_keys: Additional keys to omit from
            ``phytomni_state`` (e.g. ``main_response`` when it duplicates
            content already present in ``final_response``).

    Returns:
        Shallow copy of the final-response dict with ``phytomni_state``
        attached. A missing or non-mapping ``final_response`` falls back
        to an empty base so intermediate state still surfaces.
    """
    base = final_state.get(final_response_key)
    if not isinstance(base, Mapping):
        base = {}
    excluded = {final_response_key, *extra_excluded_keys}
    intermediate = {
        key: value
        for key, value in final_state.items()
        if key not in excluded and not key.startswith("_")
    }
    return {**base, "phytomni_state": intermediate}
