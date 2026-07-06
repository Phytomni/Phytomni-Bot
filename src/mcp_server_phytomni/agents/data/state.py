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

from typing import Any, Required, TypedDict


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

    final_response: dict[str, Any]


class DataState(TypedDict):
    """Full working state spanning input, intermediate, and output.

    Field set mirrors the legacy ``DataAgentState`` plus two
    optional keys (``chat_payload`` / ``chat_response``) that the
    rewrite prep+post split needs when the chat flag is on (the prep
    node stages the ``ChatInput`` dict on ``chat_payload`` and the
    shared chat node writes its return to ``chat_response`` for the
    post node to consume) and three optional keys
    (``pending_post_knowledge`` / ``knowledge_payload`` /
    ``knowledge_response``) that the retrieve prep+post split needs
    when the knowledge flag is on (the prep node stages the
    ``KnowledgeInput`` dict on ``knowledge_payload`` plus the
    post-knowledge node name on ``pending_post_knowledge`` and the
    shared knowledge node writes its return to ``knowledge_response``
    for the post node to consume). All five default to absence and
    are ignored on the legacy single-node paths.
    """

    user_query: str
    is_rewrite: bool
    retrieve_prompt: str
    rewrite_query: str
    final_response: dict
    chat_payload: dict[str, Any] | None
    chat_response: dict[str, Any] | None
    pending_post_knowledge: str | None
    knowledge_payload: dict[str, Any] | None
    knowledge_response: dict[str, Any] | None


DataAgentState = DataState
