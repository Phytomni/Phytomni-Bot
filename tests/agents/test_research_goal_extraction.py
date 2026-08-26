# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Evidence-backed Research goal provider on the shared extraction seam."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
)
from mcp_server_phytomni.agents.research.goal_extraction import (
    MAX_GOAL_EVIDENCE_CHARS,
    EvidenceGoalProvider,
    ResearchGoalExtractionDependencies,
)
from mcp_server_phytomni.config.defaults import InSilicoResearchConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.agents.test_research_planning import (
    _canonical_figure_goals,
    _evidence,
)

pytestmark = pytest.mark.agent

_GOAL_LOGGER = "mcp_server_phytomni.agents.research.goal_extraction"


def _content(goals: list[dict[str, str]]) -> dict[str, object]:
    """Wrap a goals-list payload in chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(goals)}}]}


def _dependencies(
    chat_app: object,
    prompt: str = "prompt-stub",
) -> ResearchGoalExtractionDependencies:
    """Build extraction seams around a stub chat application."""
    return ResearchGoalExtractionDependencies(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        prompt_builder=lambda *_a, **_kw: prompt,
        chat_app_factory=lambda: chat_app,
    )


async def test_evidence_goal_provider_returns_extractor_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider preserves extractor order and logs the extracted count."""
    payload = [
        {"goal": item.goal, "context": item.context or ""}
        for item in _canonical_figure_goals()
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content(payload)})
    )
    provider = EvidenceGoalProvider(dependencies=_dependencies(chat_app))

    with caplog.at_level(logging.INFO, logger=_GOAL_LOGGER):
        goals = await provider.extract(_evidence(), "en-US")

    assert provider.contract_name == "research_goal_provider"
    assert goals[0].goal.startswith("Replicate Figure 2:")
    assert goals[3].goal.startswith("Replicate Figure 1:")
    assert "Extracted 5 research goals" in caplog.text
    chat_app.ainvoke.assert_awaited_once()


async def test_evidence_goal_provider_rejects_invalid_evidence() -> None:
    """Malformed evidence fails closed before the chat seam is awaited."""
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content([])})
    )
    provider = EvidenceGoalProvider(dependencies=_dependencies(chat_app))

    with pytest.raises(Exception) as caught:
        await provider.extract(
            cast(ExtractedResearchEvidence, object()),
            "en-US",
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    chat_app.ainvoke.assert_not_awaited()


async def test_evidence_goal_provider_rejects_oversize_evidence() -> None:
    """Oversize retained evidence fails closed before chat is awaited."""
    text = "x" * (MAX_GOAL_EVIDENCE_CHARS + 1)
    unit = replace(
        _evidence().units[0],
        text=text,
        content_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    evidence = replace(_evidence(), units=(unit,))
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content([])})
    )
    provider = EvidenceGoalProvider(dependencies=_dependencies(chat_app))

    with pytest.raises(Exception) as caught:
        await provider.extract(evidence, "en-US")

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    chat_app.ainvoke.assert_not_awaited()
