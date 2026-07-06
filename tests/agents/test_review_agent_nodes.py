# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DeepResearchAgent class methods on agent.py.

Direct tests for ``_chat`` (LLM kwarg threading + response_format
override branch). The per-dimension draft fan-out (``draft_dispatch`` →
``draft_worker_node`` → ``draft_reduce_node``) is covered by
test_review_draft_fan_out; the mixin nodes (plan/retrieve/review/
revise/summary/post_process) are covered by their per-mixin test files;
the wrapper review_agent_function is covered by test_wrapper_smoke.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent

pytestmark = pytest.mark.agent


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
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
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
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(review_agent, "phyto_chat", fake_phyto_chat)

    schema_override: dict[str, str | dict] = {
        "type": "json_schema",
        "json_schema": {"type": "object"},
    }
    await _AgentProbe().chat("hi", response_format_override=schema_override)

    assert captured["response_format"] == schema_override
