# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Boundary coverage for the Research planning validators."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.planning import build_research_plan
from tests.agents.test_research_planning import (
    _evidence,
    _GoalProvider,
    _request,
)

pytestmark = pytest.mark.agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prepared",
    [
        object(),
        cast(
            Any,
            replace(
                cast(Any, _request().prepared),
                obs_file_list=["mutable"],
            ),
        ),
        cast(
            Any,
            replace(
                cast(Any, _request().prepared),
                data_list={"ref": "description"},
            ),
        ),
        cast(
            Any,
            replace(
                cast(Any, _request().prepared),
                data_list=MappingProxyType(
                    {
                        f"reference-{index}": "description"
                        for index in range(257)
                    }
                ),
            ),
        ),
    ],
)
async def test_planner_rejects_invalid_prepared_projection(
    prepared: Any,
) -> None:
    """Native projection violations stop before the provider boundary."""
    provider = _GoalProvider(())
    request = replace(_request(), prepared=prepared)
    with pytest.raises(Exception) as failure:
        await build_research_plan(request, provider)
    assert getattr(failure.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_planner_rejects_evidence_identity_errors_before_provider() -> (
    None
):
    """Evidence identity and parser-span errors fail before goal extraction."""
    base = _evidence().units[0]
    candidates = (
        cast(Any, replace(cast(Any, base), source_kind="unknown")),
        replace(base, source_ordinal=True),
        replace(base, text=" "),
        replace(base, content_digest="f" * 64),
        cast(Any, replace(cast(Any, base), dataset_ids=["dataset_001"])),
        replace(base, dataset_ids=("dataset_001", "dataset_001")),
        replace(base, source_span=SourceSpan(3, 2, "standalone_tab")),
    )
    for candidate in candidates:
        evidence = ExtractedResearchEvidence(
            units=(candidate,),
            document_digests=(),
            coverage_digest=_evidence().coverage_digest,
        )
        provider = _GoalProvider(())
        with pytest.raises(Exception) as failure:
            await build_research_plan(
                replace(_request(), evidence=evidence), provider
            )
        assert getattr(failure.value, "code", None) == (
            "research_input_resolution_failed"
        )
        assert provider.calls == 0
