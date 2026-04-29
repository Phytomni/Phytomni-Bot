# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Phytomni MCP Server - A comprehensive plant science research platform.

This package provides Model Context Protocol (MCP) server implementation for
advanced plant science research capabilities. It offers specialized AI agents
for various biological analysis tasks including gene function analysis,
literature research, bioinformatics workflows, and data management.

Key Components:
    - Chat agents for interactive research assistance
    - Knowledge agents for literature retrieval and RAG systems
    - Data agents for natural language to SQL queries
    - Analyst agents for bioinformatics workflow management
    - Deep genome agents for comprehensive gene function analysis
    - Review agents for systematic literature research
    - Task management system for workflow orchestration
    - Utility functions for data processing and integration

The platform integrates with cloud storage (OBS), language models, and
specialized biological databases to provide a complete research environment
for plant scientists.

Authors:
    xieshang (xieshang0608@gmail.com)
    guxiaofeng (guxiaofeng@caas.cn)

Copyright:
    Biotechnology Research Institute, Chinese Academy of Agricultural Sciences
    2024-2026. All rights reserved.
"""

from .analyst_agents import create_output_dir, get_data_list
from .analyst_agents import retrieve_plan_submit, wait_for_completion
from .chat_agents import phyto_chat, phyto_chat_with_follow
from .data_agents import nl2sql, rewrite_nl2sql
from .deep_genome_agents import gene_function
from .in_silico_research_agents import in_silico_research
from .knowledge_agents import multi_retrieve, multi_retrieve_generate
from .knowledge_agents import response_to_string, retrieve_generate
from .review_agents import deep_research
from .task_manager import create_task, TaskManager, update_task
from .utils import download_list_convert, get_prompt, get_token, split_list

__all__ = [
    "create_output_dir",
    "create_task",
    "deep_research",
    "download_list_convert",
    "gene_function",
    "get_data_list",
    "get_prompt",
    "get_token",
    "in_silico_research",
    "multi_retrieve",
    "multi_retrieve_generate",
    "nl2sql",
    "phyto_chat",
    "phyto_chat_with_follow",
    "response_to_string",
    "retrieve_generate",
    "retrieve_plan_submit",
    "rewrite_nl2sql",
    "split_list",
    "TaskManager",
    "update_task",
    "wait_for_completion",
]
