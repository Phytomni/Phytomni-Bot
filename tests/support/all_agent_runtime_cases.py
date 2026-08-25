# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic provider-edge fixtures for real all-Agent acceptance."""

from __future__ import annotations

from typing import Any

REAL_HANDLER_FIXTURES: dict[
    str, tuple[str, dict[str, Any], dict[str, Any]]
] = {
    "chat": (
        "phyto_chat_with_follow",
        {"user_query": "Explain rice tillering.", "obs_file_list": []},
        {
            "choices": [
                {"message": {"content": "chat", "follow_up_questions": []}}
            ]
        },
    ),
    "knowledge": (
        "multi_retrieve_generate",
        {"user_query": "Find rice tillering evidence.", "obs_file_list": []},
        {"choices": [{"message": {"content": "knowledge", "doc_list": []}}]},
    ),
    "data": (
        "rewrite_nl2sql",
        {"user_query": "Count rice genes."},
        {"header": [{"name": "count"}], "data": [[1]]},
    ),
    "analyst": (
        "retrieve_plan_submit",
        {
            "goal_description": "Analyze one characterized dataset.",
            "data_list": {},
            "obs_file_list": [],
        },
        {"task_id": "analyst-task", "output_dir": "/safe/analyst"},
    ),
    "review": (
        "review_agent_function",
        {"user_query": "Review rice tillering.", "obs_file_list": []},
        {"choices": [{"message": {"content": "review", "doc_list": []}}]},
    ),
    "brief_gene": (
        "brief_gene_function",
        {"user_query": "Os01g0177400"},
        {"choices": [{"message": {"content": "brief", "doc_list": []}}]},
    ),
    "deep_genome": (
        "gene_function",
        {"species_code": "osa", "gene_id": "Os01g0177400"},
        {"task_id": "genome-task", "output_dir": "/safe/genome"},
    ),
    "research": (
        "in_silico_research",
        {
            "user_query": "Reproduce the characterized study.",
            "data_list": {},
            "obs_file_list": [],
        },
        {"task_ids": ["research-task"], "output_dir": "/safe/research"},
    ),
    "design": (
        "design_module",
        {
            "species_code": "osa",
            "gene_id": "Os01g0177400",
            "obs_file_list": [],
        },
        {
            "design_task_result": [
                {"task_id": "design-task", "output_dir": "/safe/design"}
            ]
        },
    ),
    "network": (
        "network_analysis",
        {
            "species_code": "osa",
            "to_id": "TO:0000011",
            "obs_file_list": [],
        },
        {
            "network_task": {
                "task_id": "network-task",
                "output_dir": "/safe/network",
            }
        },
    ),
}

__all__ = ["REAL_HANDLER_FIXTURES"]
