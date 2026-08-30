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
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
)
from mcp_server_phytomni.agents.research.goal_extraction import (
    MAX_GOAL_EVIDENCE_CHARS,
    EvidenceGoalProvider,
    ResearchGoalExtractionDependencies,
    extract_research_goals,
    extract_research_goals_from_evidence,
)
from mcp_server_phytomni.config.defaults import InSilicoResearchConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.agents.test_research_planning import (
    _canonical_figure_goals,
    _evidence,
)

pytestmark = pytest.mark.agent

_GOAL_LOGGER = "mcp_server_phytomni.agents.research.goal_extraction"


def _content(goals: list[dict[str, Any]]) -> dict[str, object]:
    """Wrap a goals-list payload in chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(goals)}}]}


def _dependencies(
    chat_app: object,
    prompt: str = "prompt-stub",
    *,
    prompt_builder: Any | None = None,
) -> ResearchGoalExtractionDependencies:
    """Build extraction seams around a stub chat application."""
    return ResearchGoalExtractionDependencies(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        prompt_builder=prompt_builder or (lambda *_a, **_kw: prompt),
        chat_app_factory=lambda: chat_app,
    )


def _evidence_with_ids(*dataset_ids: str) -> ExtractedResearchEvidence:
    """Copy the planning fixture with a replacement inventory roster."""
    unit = replace(_evidence().units[0], dataset_ids=dataset_ids)
    return replace(_evidence(), units=(unit,))


def _paper_text(prompt_builder: MagicMock) -> str:
    """Return the evidence text passed into the prompt builder."""
    params = prompt_builder.call_args.args[2]
    assert isinstance(params, dict)
    paper_text = params["paper_text"]
    assert isinstance(paper_text, str)
    return paper_text


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


async def test_empty_chat_response_is_goal_extraction_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty chat must not look like a silent planning umbrella."""
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": {}})
    )
    with (
        caplog.at_level(logging.INFO, logger=_GOAL_LOGGER),
        pytest.raises(Exception) as caught,
    ):
        await extract_research_goals_from_evidence(
            _evidence(),
            locale="en-US",
            dependencies=_dependencies(chat_app),
        )
    assert getattr(caught.value, "code", None) == (
        "research_goal_extraction_failed"
    )
    assert getattr(caught.value, "http_status_hint", None) == 422
    assert "goal extraction failed" in caplog.text.lower()
    assert chat_app.ainvoke.await_count == 2


async def test_invalid_goal_json_retries_once_then_fails() -> None:
    """First bad JSON, second still bad: 422, never DirectGoal text."""
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                {"response": _content([])},
                {
                    "response": {
                        "choices": [{"message": {"content": "not-json"}}]
                    }
                },
            ]
        )
    )
    with pytest.raises(Exception) as caught:
        await extract_research_goals_from_evidence(
            _evidence(),
            locale="en-US",
            dependencies=_dependencies(chat_app),
        )
    assert getattr(caught.value, "code", None) == (
        "research_goal_extraction_failed"
    )
    assert chat_app.ainvoke.await_count == 2
    message = str(getattr(caught.value, "safe_message", ""))
    assert "1000" not in message


async def test_invalid_goal_json_then_valid_batch_returns_goals() -> None:
    """One bounded retry on the same evidence may recover."""
    payload = [
        {"goal": item.goal, "context": item.context or ""}
        for item in _canonical_figure_goals()[:2]
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                {"response": {"choices": [{"message": {"content": "{"}}]}},
                {"response": _content(payload)},
            ]
        )
    )
    goals = await extract_research_goals_from_evidence(
        _evidence(),
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    assert len(goals) == 2
    assert chat_app.ainvoke.await_count == 2


async def test_evidence_prompt_includes_dataset_ids_roster() -> None:
    """Units with inventory ids expose a roster line above unit text."""
    prompt_builder = MagicMock(return_value="prompt-stub")
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": _content([{"goal": "Investigate drought"}])
            }
        )
    )
    await extract_research_goals_from_evidence(
        _evidence(),
        locale="en-US",
        dependencies=_dependencies(
            chat_app, prompt_builder=prompt_builder
        ),
    )
    assert _paper_text(prompt_builder) == (
        "[evidence_001]\n"
        "dataset_ids: dataset_001\n"
        "Drought response in rice."
    )


async def test_evidence_prompt_omits_roster_when_dataset_ids_empty() -> None:
    """Empty dataset_ids must not emit a roster line."""
    prompt_builder = MagicMock(return_value="prompt-stub")
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": _content([{"goal": "Investigate drought"}])
            }
        )
    )
    await extract_research_goals_from_evidence(
        _evidence_with_ids(),
        locale="en-US",
        dependencies=_dependencies(
            chat_app, prompt_builder=prompt_builder
        ),
    )
    assert _paper_text(prompt_builder) == (
        "[evidence_001]\nDrought response in rice."
    )


async def test_evidence_prompt_joins_multiple_dataset_ids() -> None:
    """Several inventory ids join with a comma-space separator."""
    prompt_builder = MagicMock(return_value="prompt-stub")
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": _content([{"goal": "Investigate drought"}])
            }
        )
    )
    await extract_research_goals_from_evidence(
        _evidence_with_ids("dataset_001", "dataset_002"),
        locale="en-US",
        dependencies=_dependencies(
            chat_app, prompt_builder=prompt_builder
        ),
    )
    assert _paper_text(prompt_builder) == (
        "[evidence_001]\n"
        "dataset_ids: dataset_001, dataset_002\n"
        "Drought response in rice."
    )


async def test_evidence_prompt_counts_roster_toward_char_budget() -> None:
    """The dataset_ids roster line counts toward MAX_GOAL_EVIDENCE_CHARS."""
    header = "[evidence_001]\n"
    roster = "dataset_ids: dataset_001\n"
    text = "x" * (MAX_GOAL_EVIDENCE_CHARS - len(header) - len(roster) + 1)
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


async def test_extract_schema_includes_optional_dataset_ids() -> None:
    """JSON schema allows a bounded dataset_ids array beside goal."""
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": _content([{"goal": "Investigate drought"}])
            }
        )
    )
    await extract_research_goals_from_evidence(
        _evidence(),
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    chat_input = chat_app.ainvoke.await_args.args[0]
    items = chat_input["chat_kwargs"]["response_format"]["json_schema"][
        "items"
    ]
    assert items["additionalProperties"] is False
    assert items["required"] == ["goal"]
    assert items["properties"]["dataset_ids"] == {
        "type": "array",
        "items": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        },
        "maxItems": 256,
    }


async def test_chat_stub_dataset_ids_list_parses_to_tuple() -> None:
    """A JSON array of inventory ids binds onto ResearchGoal.dataset_ids."""
    payload = [
        {
            "goal": "Investigate drought",
            "context": "rice",
            "dataset_ids": ["dataset_001"],
        }
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content(payload)})
    )
    goals = await extract_research_goals_from_evidence(
        _evidence(),
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    assert goals[0].dataset_ids == ("dataset_001",)
    chat_app.ainvoke.assert_awaited_once()


async def test_chat_stub_dataset_ids_string_fail_opens_unbound() -> None:
    """A string dataset_ids value still yields the goal, unbound."""
    payload = [
        {
            "goal": "Investigate drought",
            "context": "rice",
            "dataset_ids": "dataset_001",
        }
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content(payload)})
    )
    goals = await extract_research_goals_from_evidence(
        _evidence(),
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    assert goals[0].goal == "Investigate drought"
    assert goals[0].dataset_ids is None
    chat_app.ainvoke.assert_awaited_once()


async def test_extract_research_goals_includes_bound_dataset_ids() -> None:
    """The list-of-dicts seam copies bound ids and omits unbound ones."""
    bound_payload = [
        {
            "goal": "Investigate drought",
            "context": "rice",
            "dataset_ids": ["dataset_001"],
        }
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": _content(bound_payload)})
    )
    bound = await extract_research_goals(
        "paper",
        [],
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    assert bound == [
        {
            "goal": "Investigate drought",
            "context": "rice",
            "dataset_ids": ["dataset_001"],
        }
    ]

    unbound_payload = [
        {"goal": "Investigate drought", "context": "rice"}
    ]
    chat_app = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={"response": _content(unbound_payload)}
        )
    )
    unbound = await extract_research_goals(
        "paper",
        [],
        locale="en-US",
        dependencies=_dependencies(chat_app),
    )
    assert unbound == [
        {"goal": "Investigate drought", "context": "rice"}
    ]
