# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline integration smoke between the MCP handler and the agent layer.

Exercises handle_chat_agent end-to-end with the real handler_support
kwargs builders, the real ChatAgent schema, the real phyto_chat_with_follow
wrapper, and the chat service module — only the outermost ``phyto_chat``
LLM call is mocked. Catches drift between the dispatch chokepoint and
the agent wrapper without booting a live LLM.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.mcp.handlers import handle_chat_agent
from mcp_server_phytomni.mcp.schemas import ChatAgent

pytestmark = pytest.mark.integration


async def test_handle_chat_agent_routes_args_to_chat_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat handler threads its kwargs into phyto_chat unchanged.

    Pins the handler→handler_support→wrapper→service chain by mocking
    only the outermost LLM call. The test asserts the response payload
    matches what the service returned (so the wrapper's optional follow-up
    enrichment did not silently mutate the shape) and that the kwargs
    every layer cares about (api_key, base_url, model, max_tokens,
    response_format) actually reached phyto_chat.
    """
    captured_calls: list[dict[str, Any]] = []

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        """Record each phyto_chat call and return a minimal completion.

        phyto_chat_with_follow calls phyto_chat twice — once with the
        user query, once with a follow-up-question prompt — so the test
        keeps the per-call kwargs in order and asserts against the first.
        """
        captured_calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "Smoke-test answer.",
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    monkeypatch.setattr(chat_service, "phyto_chat", fake_phyto_chat)

    args = ChatAgent(user_query="What is photosynthesis?", obs_file_list=[])
    result = await handle_chat_agent(args)

    assert isinstance(result, dict)
    assert result["choices"][0]["message"]["content"] == "Smoke-test answer."
    assert len(captured_calls) == 2
    initial_kwargs = captured_calls[0]
    assert initial_kwargs["user_query"] == "What is photosynthesis?"
    assert initial_kwargs["api_key"] == "pytest-api-key"
    assert initial_kwargs["base_url"] == "https://example.invalid/llm"
    assert initial_kwargs["model"] == "pytest-model"
    assert "max_tokens" in initial_kwargs
    assert "response_format" in initial_kwargs
