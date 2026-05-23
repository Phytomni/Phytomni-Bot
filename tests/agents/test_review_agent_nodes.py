# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DeepResearchAgent class methods on agent.py.

Direct tests for ``_chat`` (LLM kwarg threading + response_format
override branch) and ``draft_node`` (per-dimension parallel chat fan-out
with BaseException fallback). The mixin nodes (plan/retrieve/review/
revise/summary/post_process) are covered by their per-mixin test files;
the wrapper review_agent_function is covered by test_wrapper_smoke.
"""

from __future__ import annotations

from typing import Any, Dict, Union, cast

import pytest

from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.agents.review.agent import (
    DeepResearchAgent,
    DeepResearchState,
)

pytestmark = pytest.mark.agent


def _agent() -> DeepResearchAgent:
    """Build a DeepResearchAgent with conftest-injected dummy credentials."""
    return DeepResearchAgent()


class _AgentProbe(DeepResearchAgent):
    """Public-named proxy so tests exercise protected ``_chat`` in-class.

    Tests probe through a subclass so the protected ``_chat`` access
    stays inside the class hierarchy and does not trip pylint W0212 on
    the test module — same pattern the mixin tests use for their own
    protected helpers.
    """

    async def chat(
        self,
        prompt: str,
        response_format_override: Any = None,
    ) -> Any:
        """Public proxy for the protected ``_chat`` helper."""
        return await self._chat(prompt, response_format_override)


async def test_chat_threads_review_config_into_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_chat forwards review_config / sensitive_config into phyto_chat.

    Pins the contract that the configured LLM call carries the test-env
    api_key / base_url / model and the default response_format from
    review_config when no override is passed. This is the only direct
    coverage for line 153 of agent.py.
    """
    captured: Dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(review_agent, "phyto_chat", fake_phyto_chat)

    agent = _AgentProbe()
    result = await agent.chat("hello question")

    assert result == {"choices": [{"message": {"content": "ok"}}]}
    assert captured["user_query"] == "hello question"
    assert captured["api_key"] == "pytest-api-key"
    assert captured["base_url"] == "https://example.invalid/llm"
    assert captured["model"] == "pytest-model"
    assert captured["prompt_file"] == agent.review_config.PROMPT_FILE
    assert captured["prompt_path"] == agent.review_config.PROMPT_PATH
    assert captured["response_format"] == agent.review_config.RESPONSE_FORMAT
    assert captured["timeout"] == agent.review_config.TIMEOUT
    assert captured["max_retries"] == agent.review_config.MAX_RETRIES
    assert captured["retriable_codes"] == agent.review_config.RETRIABLE_CODES


async def test_chat_uses_response_format_override_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_chat substitutes response_format with the override when set.

    Pins the JSON-schema override path used by plan_node (the only
    in-tree caller that passes a non-None response_format_override) so
    a regression of the ``or self.review_config.RESPONSE_FORMAT``
    fallback would surface here without needing the full plan_node run.
    """
    captured: Dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(review_agent, "phyto_chat", fake_phyto_chat)

    schema_override: Dict[str, Union[str, Dict]] = {
        "type": "json_schema",
        "json_schema": {"type": "object"},
    }
    await _AgentProbe().chat("hi", response_format_override=schema_override)

    assert captured["response_format"] == schema_override


async def test_draft_node_runs_one_chat_per_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """draft_node fans out to _chat per dimension and packs draft_contents.

    Stubs the agent's own ``_chat`` so the test exercises ``draft_node``
    in isolation from the LLM kwarg threading already pinned above.
    Also stubs ``get_prompt`` so the prompt path is observable without
    the real ``.prompts.yaml`` resolver.
    """
    agent = _agent()
    prompt_paths: list[str] = []
    chat_prompts: list[str] = []

    def fake_get_prompt(
        _prompt_file: str, prompt_path: str, params: Dict[str, Any]
    ) -> str:
        prompt_paths.append(prompt_path)
        return f"PROMPT::{params['subtopic']}::{params['knowledge']}"

    async def fake_chat(
        prompt: str, response_format_override: Any = None
    ) -> Dict[str, Any]:
        del response_format_override
        chat_prompts.append(prompt)
        return {
            "choices": [{"message": {"content": f"draft#{len(chat_prompts)}"}}]
        }

    monkeypatch.setattr(review_agent, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(agent, "_chat", fake_chat)

    state: Dict[str, Any] = {
        "dimension_params": [
            {"subtopic": "Genetics", "knowledge": "k1"},
            {"subtopic": "Physiology", "knowledge": "k2"},
            {"subtopic": "Breeding", "knowledge": "k3"},
        ],
    }

    result = await agent.draft_node(cast(DeepResearchState, state))

    assert result == {
        "draft_contents": ["draft#1", "draft#2", "draft#3"],
    }
    assert prompt_paths == [
        "user/deep_research_dimension",
        "user/deep_research_dimension",
        "user/deep_research_dimension",
    ]
    assert chat_prompts == [
        "PROMPT::Genetics::k1",
        "PROMPT::Physiology::k2",
        "PROMPT::Breeding::k3",
    ]


async def test_draft_node_substitutes_empty_string_on_chat_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-dimension chat exception yields an empty draft slot, not a raise.

    Pins the ``return_exceptions=True`` + ``isinstance(result, BaseException)``
    -> '' fallback path. Without this the gather raise would propagate
    and bring down the whole review run on a single transient hiccup.
    """
    agent = _agent()
    call_index = {"n": 0}

    async def fake_chat(prompt: str, response_format_override: Any = None):
        del prompt, response_format_override
        call_index["n"] += 1
        if call_index["n"] == 2:
            raise RuntimeError("transient backend hiccup")
        return {
            "choices": [{"message": {"content": f"draft#{call_index['n']}"}}]
        }

    monkeypatch.setattr(review_agent, "get_prompt", lambda *_a, **_k: "PROMPT")
    monkeypatch.setattr(agent, "_chat", fake_chat)

    state: Dict[str, Any] = {
        "dimension_params": [
            {"subtopic": "A", "knowledge": "kA"},
            {"subtopic": "B", "knowledge": "kB"},
            {"subtopic": "C", "knowledge": "kC"},
        ],
    }

    result = await agent.draft_node(cast(DeepResearchState, state))

    assert result == {"draft_contents": ["draft#1", "", "draft#3"]}


async def test_draft_node_returns_empty_list_when_no_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero dimensions yields an empty draft list with no chat calls."""
    agent = _agent()
    chat_calls: list[str] = []

    async def fake_chat(prompt: str, response_format_override: Any = None):
        del response_format_override
        chat_calls.append(prompt)
        return {}

    monkeypatch.setattr(agent, "_chat", fake_chat)

    result = await agent.draft_node(
        cast(DeepResearchState, {"dimension_params": []})
    )

    assert result == {"draft_contents": []}
    assert not chat_calls
