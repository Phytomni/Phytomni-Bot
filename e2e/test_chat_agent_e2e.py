# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``ChatAgent`` over the stdio MCP client.

Submits the committed ``demo_data/payloads/chat_agent.json`` payload
through ``PhytomniMcpClient`` and asserts the returned answer mentions
at least one canonical photosynthesis keyword. Runs only when the
suite is invoked manually with a configured ``.env``.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_chat_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live

EXPECT_REASONING_VAR = "PHYTOMNI_E2E_EXPECT_REASONING"


def _expect_reasoning_enabled() -> bool:
    """Return True when the opt-in reasoning-content assertion is on.

    Truthy values: ``1`` / ``true`` / ``yes`` (case-insensitive).
    Default off so non-reasoning backends do not fail the suite.
    """
    return os.environ.get(EXPECT_REASONING_VAR, "").lower() in {
        "1",
        "true",
        "yes",
    }


async def test_chat_agent_e2e_returns_photosynthesis_answer(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """ChatAgent answers the C3 query with photosynthesis-related text.

    When ``PHYTOMNI_E2E_EXPECT_REASONING`` is set, additionally assert
    that ``raw.choices[0].message.reasoning_content`` is a non-empty
    string. Opt-in because the default deployment model matrix is not
    a reasoning model and emitting an empty trace is legitimate.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("chat_agent.json")

    response = await call_tool(mcp_client, "ChatAgent", payload)

    assert_chat_answer(response.formatted.answer)

    if _expect_reasoning_enabled():
        assert isinstance(response.raw_payload, dict)
        raw = response.raw_payload.get("raw")
        assert isinstance(raw, dict), (
            "PHYTOMNI_E2E_EXPECT_REASONING requires envelope.raw dict; "
            f"got: {type(raw).__name__}"
        )
        choices = raw.get("choices")
        assert (
            isinstance(choices, list) and choices
        ), "PHYTOMNI_E2E_EXPECT_REASONING requires non-empty raw.choices"
        message = choices[0].get("message")
        assert isinstance(
            message, dict
        ), "PHYTOMNI_E2E_EXPECT_REASONING requires raw.choices[0].message"
        reasoning = message.get("reasoning_content")
        assert isinstance(reasoning, str) and reasoning.strip(), (
            "PHYTOMNI_E2E_EXPECT_REASONING set but "
            f"raw.choices[0].message.reasoning_content was: {reasoning!r}"
        )
