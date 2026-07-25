# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public IO schemas for the KnowledgeAgent subgraph.

``KnowledgeInput`` is the narrow request shape a parent graph
supplies; ``KnowledgeOutput`` carries both answer paths
(``final_response`` and ``retrieved_docs``); ``KnowledgeState`` is
the full working dict and stays binary-compatible with the legacy
``KnowledgeAgentState`` alias so internal node annotations remain
valid.
"""

# NOTE: ``from __future__ import annotations`` is deliberately
# omitted. PEP 705 ``Required[]`` markers are erased by lazy
# annotations and ``TypedDict.__required_keys__`` is computed at
# class-definition time, so future annotations would silently drop
# every ``Required[]`` marker on ``KnowledgeInput`` and
# ``KnowledgeState``.

from typing import Any, Required, TypedDict

from ...runtime.locale import SupportedLocale


class KnowledgeInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting knowledge.

    ``user_query`` is the only Required key so a parent owes the
    subgraph one field; remaining keys default in ``arun`` or the
    relevant node when absent. ``is_generate`` toggles the
    retrieve-only versus retrieve-plus-generate path and
    ``is_follow_up`` gates the trailing follow-up-question call.
    """

    user_query: Required[str]
    locale: SupportedLocale
    obs_file_list: list[str] | None
    repo_id_dict: dict[str, int] | None
    is_generate: bool
    is_follow_up: bool


class KnowledgeOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Both keys ship populated because the same compiled subgraph
    serves the retrieve-only and the generate paths: callers pick
    one based on the originally requested ``is_generate`` flag,
    mirroring the existing ``KnowledgeAgent.arun`` bifurcation.
    """

    retrieved_docs: list[dict[str, Any]]
    final_response: dict[str, Any]


class KnowledgeState(TypedDict):
    """Full working state spanning input, intermediate, and output.

    Field set mirrors the legacy ``KnowledgeAgentState`` plus three
    optional keys (``pending_post`` / ``chat_payload`` /
    ``chat_response``) that the prep+post split needs when the chat
    flag is on: prep nodes stage the post-chat node name on
    ``pending_post`` and the ``ChatInput`` dict on ``chat_payload``;
    the shared chat node writes its return to ``chat_response`` for
    the post node to consume. All three default to absence and are
    ignored on the legacy single-node path.
    """

    user_query: str
    locale: SupportedLocale
    obs_file_list: list[str] | None
    repo_id_dict: dict[str, int] | None
    upload_context: str
    retrieved_docs: list[dict[str, Any]]
    retrieve_context: str
    main_response: dict[str, Any]
    is_generate: bool
    is_follow_up: bool
    follow_up_questions: list[dict]
    final_response: dict[str, Any]
    pending_post: str | None
    chat_payload: dict[str, Any] | None
    chat_response: dict[str, Any] | None


KnowledgeAgentState = KnowledgeState
