# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Local/resumable public Agents share the canonical Runtime boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("slug", "driver", "topology", "join", "checkpoint", "resume"),
    [
        (
            "chat",
            "resumable_graph",
            "conditional",
            "not_applicable",
            "graph",
            "action_and_recovery",
        ),
        (
            "knowledge",
            "local_graph",
            "conditional",
            "not_applicable",
            "none",
            "none",
        ),
        ("data", "local_graph", "serial", "not_applicable", "none", "none"),
        (
            "brief_gene",
            "local_graph",
            "hybrid",
            "best_effort",
            "none",
            "none",
        ),
        (
            "review",
            "resumable_graph",
            "parallel",
            "all",
            "graph",
            "action_and_recovery",
        ),
    ],
)
def test_local_cohort_catalog_declares_one_runtime_semantics(
    slug: str,
    driver: str,
    topology: str,
    join: str,
    checkpoint: str,
    resume: str,
) -> None:
    from mcp_server_phytomni.public_agent_catalog import public_agent_spec

    spec = public_agent_spec(slug)
    assert spec is not None
    assert (
        spec.driver,
        spec.topology,
        spec.join,
        spec.checkpoint,
        spec.resume,
    ) == (driver, topology, join, checkpoint, resume)


@pytest.mark.parametrize(
    "slug", ["chat", "knowledge", "data", "brief_gene", "review"]
)
def test_local_cohort_preserves_business_value_and_terminal_replay(
    tmp_path: Path,
    slug: str,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    expected = {"agent": slug, "business": "unchanged"}
    calls = 0

    async def business_call():
        nonlocal calls
        calls += 1
        return expected

    db_path = tmp_path / f"{slug}.db"
    execution_id = f"turn-{slug}"
    first = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=execution_id,
            agent_slug=slug,
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )
    assert first == expected
    assert calls == 1
    projection = SQLiteExecutionJournal(str(db_path)).get_projection(
        execution_id,
        owner="alice",
    )
    assert projection.status.value == "succeeded"
    assert projection.terminal is not None
    replay = SQLiteExecutionJournal(str(db_path)).get_projection(
        execution_id,
        owner="alice",
    )
    assert replay == projection
    assert calls == 1


def test_agent_modules_do_not_bypass_shared_graph_runner() -> None:
    root = Path(__file__).parents[2] / "src/mcp_server_phytomni/agents"
    bypasses: list[str] = []
    for source in root.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        if ".ainvoke(" in text or ".astream(" in text:
            bypasses.append(source.relative_to(root).as_posix())
    assert bypasses == []
