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

from typing import Any, Dict, List, Optional, Required, TypedDict


class KnowledgeInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting knowledge.

    ``user_query`` is the only Required key so a parent owes the
    subgraph one field; remaining keys default in ``arun`` or the
    relevant node when absent. ``is_generate`` toggles the
    retrieve-only versus retrieve-plus-generate path and
    ``is_follow_up`` gates the trailing follow-up-question call.
    """

    user_query: Required[str]
    obs_file_list: Optional[List[str]]
    repo_id_dict: Optional[Dict[str, int]]
    is_generate: bool
    is_follow_up: bool


class KnowledgeOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Both keys ship populated because the same compiled subgraph
    serves the retrieve-only and the generate paths: callers pick
    one based on the originally requested ``is_generate`` flag,
    mirroring the existing ``KnowledgeAgent.arun`` bifurcation.
    """

    retrieved_docs: List[Dict[str, Any]]
    final_response: Dict[str, Any]


class KnowledgeState(TypedDict):
    """Full working state spanning input, intermediate, and output.

    Field set is identical to the legacy ``KnowledgeAgentState``
    so existing node annotations and ``arun`` initial-state dicts
    remain valid; the only material change is the move into a
    dedicated module to keep the IO contract decoupled from the
    agent class file.
    """

    user_query: str
    obs_file_list: Optional[List[str]]
    repo_id_dict: Optional[Dict[str, int]]
    upload_context: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    main_response: Dict[str, Any]
    is_generate: bool
    is_follow_up: bool
    follow_up_questions: List[dict]
    final_response: Dict[str, Any]


KnowledgeAgentState = KnowledgeState
