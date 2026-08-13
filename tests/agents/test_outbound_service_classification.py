# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Table-driven ownership checks for outbound service families."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from mcp_server_phytomni.api.relay.routes import (
    _ANALYSIS_LIFECYCLE,
    _OPENAI_RELAYS,
    _PLATFORM_RELAYS,
)
from mcp_server_phytomni.common.relay_client import (
    RelayClient,
    RelayRequestOptions,
)
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
        ("agents/shared/sql.py", OutboundPoolName.BI),
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
    ("path", "method", "expected"),
    [
        ("llm/chat/completions", "POST", OutboundPoolName.LLM),
        ("coder/chat/completions", "POST", OutboundPoolName.LLM),
        ("embed/embeddings", "POST", OutboundPoolName.LLM),
        ("retrieve/search", "POST", OutboundPoolName.RETRIEVAL),
        ("rerank/rank", "POST", OutboundPoolName.RERANK),
        ("database/nl2sql", "POST", OutboundPoolName.NL2SQL),
        (
            "analysis/tasks",
            "POST",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        (
            "analysis/task-marker",
            "GET",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "analysis/task-marker/logs",
            "GET",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "analysis/task-marker/result",
            "GET",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "analysis/task-marker/terminate",
            "POST",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        ("bi/query", "POST", OutboundPoolName.BI),
        ("obs/object", "GET", OutboundPoolName.OBS),
        (
            "capabilities",
            "GET",
            OutboundPoolName.RELAY_CONTROL,
        ),
        (
            "research-input/object-grants",
            "POST",
            OutboundPoolName.RELAY_CONTROL,
        ),
    ],
)
async def test_generic_relay_calls_require_caller_supplied_final_pool(
    outbound_runtime: Any,
    path: str,
    method: str,
    expected: OutboundPoolName,
) -> None:
    """Every generic relay call records exactly the caller's typed pool."""
    outbound_runtime.transport.enqueue(content=b"{}")
    client = RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("recording-key"),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )

    if method == "GET":
        await client.get_json(
            path,
            pool=expected,
            options=RelayRequestOptions(
                message="recording relay request failed"
            ),
        )
    else:
        await client.post_json(
            path,
            {"marker": "classification"},
            pool=expected,
            options=RelayRequestOptions(
                message="recording relay request failed"
            ),
        )

    assert outbound_runtime.runtime.pools.snapshot(expected).started == 1


def test_operator_relay_operations_have_one_final_pool_mapping() -> None:
    """Every HTTP-forwarding route declares one typed final-service pool."""
    openai_mappings = tuple(
        (name, path, pool) for name, path, _, _, pool in _OPENAI_RELAYS
    )
    assert openai_mappings == (
        ("llm", "chat/completions", OutboundPoolName.LLM),
        ("coder", "chat/completions", OutboundPoolName.LLM),
        ("embed", "embeddings", OutboundPoolName.LLM),
    )
    platform_mappings = tuple(
        (name, path, pool) for name, path, _, _, _, pool in _PLATFORM_RELAYS
    )
    assert platform_mappings == (
        ("retrieve", "search", OutboundPoolName.RETRIEVAL),
        ("rerank", "rank", OutboundPoolName.RERANK),
        ("database", "nl2sql", OutboundPoolName.NL2SQL),
        ("analysis", "tasks", OutboundPoolName.ANALYSIS_CONTROL),
    )
    lifecycle_mappings = tuple(
        (suffix, method, operation, pool)
        for suffix, method, _, operation, pool in _ANALYSIS_LIFECYCLE
    )
    assert lifecycle_mappings == (
        ("", "GET", "analysis_status", OutboundPoolName.ANALYSIS_STATUS),
        (
            "/logs",
            "GET",
            "analysis_logs",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "/terminate",
            "POST",
            "analysis_terminate",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
    )


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("llm/chat/completions", "POST", OutboundPoolName.LLM),
        ("coder/chat/completions", "POST", OutboundPoolName.LLM),
        ("embed/embeddings", "POST", OutboundPoolName.LLM),
        ("retrieve/search", "POST", OutboundPoolName.RETRIEVAL),
        ("rerank/rank", "POST", OutboundPoolName.RERANK),
        ("database/nl2sql", "POST", OutboundPoolName.NL2SQL),
        ("analysis/tasks", "POST", OutboundPoolName.ANALYSIS_CONTROL),
        ("analysis/task-marker", "GET", OutboundPoolName.ANALYSIS_STATUS),
        (
            "analysis/task-marker/logs",
            "GET",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "analysis/task-marker/terminate",
            "POST",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        ("spa-faq/repository-marker", "GET", OutboundPoolName.SPA_FAQ),
        ("bi/query", "POST", OutboundPoolName.BI),
        ("obs/list", "GET", OutboundPoolName.OBS),
        ("capabilities", "GET", OutboundPoolName.RELAY_CONTROL),
        (
            "research-input/object-grants",
            "POST",
            OutboundPoolName.RELAY_CONTROL,
        ),
    ],
)
async def test_relay_child_operation_records_exactly_one_final_pool_attempt(
    outbound_runtime: Any,
    path: str,
    method: str,
    expected: OutboundPoolName,
) -> None:
    """A real local relay attempt increments only its final service pool."""
    outbound_runtime.transport.enqueue(content=b"{}")
    client = RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("recording-key"),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )
    before = {
        name: outbound_runtime.runtime.pools.snapshot(name).started
        for name in OutboundPoolName
    }

    if method == "GET":
        await client.get_json(
            path,
            pool=expected,
            options=RelayRequestOptions(
                message="recording relay request failed"
            ),
        )
    else:
        await client.post_json(
            path,
            {"marker": "classification"},
            pool=expected,
            options=RelayRequestOptions(
                message="recording relay request failed"
            ),
        )

    after = {
        name: outbound_runtime.runtime.pools.snapshot(name).started
        for name in OutboundPoolName
    }
    assert {name: after[name] - before[name] for name in OutboundPoolName} == {
        name: int(name is expected) for name in OutboundPoolName
    }
    assert len(outbound_runtime.transport.requests) == 1
