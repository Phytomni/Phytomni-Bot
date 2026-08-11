# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Table-driven ownership checks for outbound service families."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.api.relay.routes import _OPERATOR_RELAY_POOLS
from mcp_server_phytomni.common.relay_client import _relay_pool
from mcp_server_phytomni.runtime.outbound import OutboundPoolName

pytestmark = pytest.mark.agent

_SOURCE_ROOT = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"


@pytest.mark.parametrize(
    ("relative_path", "pool"),
    [
        ("auth/iam.py", OutboundPoolName.IAM),
        ("agents/chat/service.py", OutboundPoolName.LLM),
        ("agents/expert/router.py", OutboundPoolName.LLM),
        ("agents/knowledge/retrieval.py", OutboundPoolName.RETRIEVAL),
        ("agents/knowledge/retrieval.py", OutboundPoolName.RERANK),
        ("agents/evolution/agent.py", OutboundPoolName.SPA_FAQ),
        ("agents/data/nl2sql.py", OutboundPoolName.NL2SQL),
        ("agents/analyst/graph.py", OutboundPoolName.ANALYSIS_CONTROL),
        ("agents/analyst/task_ops.py", OutboundPoolName.ANALYSIS_CONTROL),
        ("agents/analyst/task_ops.py", OutboundPoolName.ANALYSIS_STATUS),
        ("common/relay_client.py", OutboundPoolName.BI),
        ("common/relay_client.py", OutboundPoolName.OBS),
    ],
)
def test_direct_service_file_declares_its_typed_pool(
    relative_path: str,
    pool: OutboundPoolName,
) -> None:
    """Every Wave 1/2 adapter names its final typed logical pool."""
    source = (_SOURCE_ROOT / relative_path).read_text(encoding="utf-8")
    assert f"OutboundPoolName.{pool.name}" in source


@pytest.mark.parametrize(
    ("service", "path", "method", "expected"),
    [
        ("llm", "chat/completions", "POST", OutboundPoolName.LLM),
        ("coder", "chat/completions", "POST", OutboundPoolName.LLM),
        ("embed", "embeddings", "POST", OutboundPoolName.LLM),
        ("retrieve", "search", "POST", OutboundPoolName.RETRIEVAL),
        ("rerank", "score", "POST", OutboundPoolName.RERANK),
        ("database", "query", "POST", OutboundPoolName.NL2SQL),
        (
            "analysis",
            "submit",
            "POST",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        (
            "analysis",
            "status",
            "GET",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        ("analysis", "delete", "DELETE", OutboundPoolName.ANALYSIS_CONTROL),
        ("bi", "query", "POST", OutboundPoolName.BI),
        ("obs", "object", "GET", OutboundPoolName.OBS),
        (
            "capabilities",
            "get",
            "GET",
            OutboundPoolName.RELAY_CONTROL,
        ),
        (
            "research-input",
            "grant",
            "POST",
            OutboundPoolName.RELAY_CONTROL,
        ),
    ],
)
def test_child_relay_paths_resolve_to_final_service_pool(
    service: str,
    path: str,
    method: str,
    expected: OutboundPoolName,
) -> None:
    """Relay-child calls never fall back to one broad relay limiter."""
    assert _relay_pool(f"{service}/{path}", method) is expected


def test_operator_relay_platform_table_has_no_broad_pool() -> None:
    """Operator platform routes carry a final typed pool mapping."""
    assert _OPERATOR_RELAY_POOLS == {
        "analysis": OutboundPoolName.ANALYSIS_CONTROL,
        "database": OutboundPoolName.NL2SQL,
        "rerank": OutboundPoolName.RERANK,
        "retrieve": OutboundPoolName.RETRIEVAL,
    }
