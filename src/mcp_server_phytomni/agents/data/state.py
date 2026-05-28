# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public IO schemas for the DataAgent subgraph.

``DataInput`` is the parent-facing request shape; ``DataOutput``
exposes the SQL response surface; ``DataState`` is the full working
dict and stays binary-compatible with the legacy ``DataAgentState``
alias so existing internal node annotations and ``cast(...)`` calls
in tests remain valid without an unrelated migration.
"""

# NOTE: ``from __future__ import annotations`` is deliberately
# omitted. PEP 705 ``Required[]`` markers are erased by lazy
# annotations and ``TypedDict.__required_keys__`` is computed at
# class-definition time, so future annotations would silently drop
# every ``Required[]`` marker on ``DataInput`` and ``DataState``.

from typing import Any, Dict, Required, TypedDict


class DataInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting data.

    ``user_query`` is the only Required key; ``is_rewrite`` defaults
    to ``True`` inside ``arun`` and toggles the ``route_start`` path
    between the retrieve-then-rewrite branch and the
    rewrite-bypassed direct-search branch.
    """

    user_query: Required[str]
    is_rewrite: bool


class DataOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Pins ``final_response`` as always present so callers read
    ``state["final_response"]`` without ``in`` guards. The value is
    the SQL database response dict produced by ``search_node``.
    """

    final_response: Dict[str, Any]


class DataState(TypedDict):
    """Full working state spanning input, intermediate, and output.

    Field set is identical to the legacy ``DataAgentState`` so
    existing node annotations and the test-only ``cast`` sites in
    ``test_data_agent*.py`` remain valid; the only material change
    is the move into a dedicated module to keep the IO contract
    decoupled from the agent class file.
    """

    user_query: str
    is_rewrite: bool
    retrieve_prompt: str
    rewrite_query: str
    final_response: dict


DataAgentState = DataState
