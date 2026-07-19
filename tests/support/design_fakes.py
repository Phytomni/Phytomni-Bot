# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-only fakes shared by design producer tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.design import agent as design_agent


def install_design_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_uri: str,
    task_id: str,
    output_dir: str,
    submit_return: dict[str, Any] | None = None,
) -> AsyncMock:
    """Install deterministic prompt, data, agent, and submit fakes."""
    monkeypatch.setattr(
        design_agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        design_agent,
        "get_data_list",
        lambda *_a, **_kw: [data_uri],
    )
    monkeypatch.setattr(
        design_agent,
        "AnalystAgent",
        lambda **_kw: "analyst-agent-stub",
    )
    submit_mock = AsyncMock(
        return_value=submit_return
        or {
            "task_id": task_id,
            "output_dir": output_dir,
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        design_agent, "submit_analyst_via_subgraph", submit_mock
    )
    return submit_mock


async def invoke_design_case(
    wrapper: Callable[..., Awaitable[dict[str, Any]]],
    submit_mock: AsyncMock,
    *,
    species_code: str,
    gene_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    """Invoke one producer wrapper and return result, request, and kwargs."""
    result = await wrapper(species_code=species_code, gene_id=gene_id)
    call_args = submit_mock.await_args
    assert call_args is not None
    return result, call_args.args[3], call_args.kwargs
