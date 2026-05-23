# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Merge LangGraph intermediate state into agent return payloads.

Final-response agents return ``final_state["final_response"]`` and
task-style agents trim to a small allow-list; both shapes discard the
rest of the LangGraph state. ``merge_intermediate_state`` lifts the
remainder into one ``phytomni_state`` block so the HTTP/MCP ``raw``
envelope can expose plan, retrieved_docs, tool_usages, and similar
intermediates without each agent inlining the merge.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = ["merge_intermediate_state"]


def merge_intermediate_state(
    final_state: Mapping[str, Any],
    final_response_key: str = "final_response",
    extra_excluded_keys: Iterable[str] = (),
    *,
    surface_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a base payload enriched with a ``phytomni_state`` block.

    Two complementary modes share one return shape:

    * Cited / final-response style (``surface_keys`` omitted): the base
      is the dict at ``final_response_key``; everything else in
      ``final_state`` (minus ``extra_excluded_keys`` and underscore
      bookkeeping) goes under ``phytomni_state``.
    * Task style (``surface_keys`` provided): the base is the subset of
      ``final_state`` keyed by ``surface_keys`` (missing keys surface as
      ``None``, mirroring the existing ``{k: result.get(k) for k in
      result_keys}`` trim semantics); the remaining state fields go
      under ``phytomni_state``.

    Args:
        final_state: LangGraph compiled graph output.
        final_response_key: State key whose value becomes the base dict
            in cited mode. Ignored when ``surface_keys`` is provided.
        extra_excluded_keys: Additional keys to omit from
            ``phytomni_state`` (e.g. ``main_response`` when it
            duplicates content already present in ``final_response``).
        surface_keys: Explicit allow-list selecting which state keys
            stay at the top level. Pass ``None`` for cited mode.

    Returns:
        Shallow copy of the base dict with ``phytomni_state`` attached.
        A missing / non-mapping cited base falls back to an empty base
        so intermediate state still surfaces.
    """
    extra = set(extra_excluded_keys)
    if surface_keys is not None:
        surface_keys_tuple = tuple(surface_keys)
        base: dict[str, Any] = {
            key: final_state.get(key) for key in surface_keys_tuple
        }
        excluded = set(surface_keys_tuple) | extra
    else:
        raw_base = final_state.get(final_response_key)
        base = dict(raw_base) if isinstance(raw_base, Mapping) else {}
        excluded = {final_response_key, *extra}
    intermediate = {
        key: value
        for key, value in final_state.items()
        if key not in excluded and not key.startswith("_")
    }
    return {**base, "phytomni_state": intermediate}
