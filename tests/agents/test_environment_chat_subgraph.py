# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-subgraph dispatch test for ``environment_region_codes``.

``environment_region_codes`` routes its code-extraction chat call
through ``_cached_chat_app().ainvoke`` with the shared
``chat_adapters`` IO mappers and lifts the raw completion from the
``ChatOutput.response`` key.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.environment import agent as environment_agent
from mcp_server_phytomni.agents.environment.agent import (
    environment_region_codes,
)

pytestmark = pytest.mark.agent


def _content(text: str) -> dict[str, object]:
    """Wrap a single chat-completion content payload for tests."""
    return {"choices": [{"message": {"content": text}}]}


async def test_environment_region_codes_uses_chat_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code extraction delegates to the compiled chat subgraph."""
    subgraph_app_mock = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "response": _content("<result>110000|110100|110108</result>"),
            }
        )
    )
    monkeypatch.setattr(
        environment_agent, "_cached_chat_app", lambda: subgraph_app_mock
    )
    monkeypatch.setattr(
        environment_agent, "load_text_file", lambda *_a, **_kw: "{}"
    )
    monkeypatch.setattr(
        environment_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await environment_region_codes("Beijing Haidian", {})

    assert result == ("110000", "110100", "110108")
    subgraph_app_mock.ainvoke.assert_awaited_once()
