# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for environment_region_codes' chat-subgraph dispatch.

Pins ``USE_CHAT_SUBGRAPH``: flag-off keeps the legacy ``phyto_chat``
call; flag-on routes through ``_cached_chat_app().ainvoke`` with the
shared ``chat_adapters`` IO mappers.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.environment import agent as environment_agent
from mcp_server_phytomni.agents.environment.agent import (
    environment_region_codes,
)

from ._subgraph_branch_fakes import install_chat_branch_mocks

pytestmark = pytest.mark.agent

_ENV_MODULE = "mcp_server_phytomni.agents.environment.agent"


def _content(text: str) -> dict[str, object]:
    """Wrap a single chat-completion content payload for tests."""
    return {"choices": [{"message": {"content": text}}]}


async def test_environment_region_codes_uses_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits the legacy ``phyto_chat`` directly."""
    monkeypatch.setattr(
        environment_agent.ENVIRONMENT_CONFIG, "USE_CHAT_SUBGRAPH", False
    )
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _ENV_MODULE,
        legacy_response=_content("<result>110000|110100|110108</result>"),
        subgraph_response={"choices": [{"message": {"content": ""}}]},
    )
    monkeypatch.setattr(
        environment_agent, "load_text_file", lambda *_a, **_kw: "{}"
    )
    monkeypatch.setattr(
        environment_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await environment_region_codes("Beijing Haidian", {})

    assert result == ("110000", "110100", "110108")
    legacy_mock.assert_awaited_once()
    subgraph_app_mock.ainvoke.assert_not_awaited()


async def test_environment_region_codes_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to the compiled chat subgraph."""
    monkeypatch.setattr(
        environment_agent.ENVIRONMENT_CONFIG, "USE_CHAT_SUBGRAPH", True
    )
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _ENV_MODULE,
        legacy_response=_content("<result>000000|000000|000000</result>"),
        subgraph_response={
            "response": _content("<result>110000|110100|110108</result>"),
        },
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
    legacy_mock.assert_not_awaited()
