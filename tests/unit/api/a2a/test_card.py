# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the public A2A Agent Card builder."""

from __future__ import annotations

import pytest
from a2a.types import AgentCard
from google.protobuf import json_format

from mcp_server_phytomni import __version__
from mcp_server_phytomni.api.a2a.card import build_agent_card

pytestmark = pytest.mark.unit


def test_agent_card_declares_one_v1_jsonrpc_interface() -> None:
    """The card exposes the stable A2A endpoint and no internal host."""
    card = build_agent_card("https://public.example/base/")

    assert card.name == "Phytomni"
    assert card.version == __version__
    assert len(card.supported_interfaces) == 1
    interface = card.supported_interfaces[0]
    assert interface.url == "https://public.example/base/a2a"
    assert interface.protocol_binding == "JSONRPC"
    assert interface.protocol_version == "1.0"
    assert "127.0.0.1" not in interface.url


def test_agent_card_capabilities_are_not_overstated() -> None:
    """Phase 1 advertises no streaming, push, or extended-card methods."""
    card = build_agent_card("https://public.example")

    assert card.capabilities.streaming is False
    assert card.capabilities.push_notifications is False
    assert card.capabilities.extended_agent_card is False
    assert len(card.skills) == 10


def test_agent_card_declares_bearer_scope_and_round_trips() -> None:
    """Security metadata and protobuf JSON serialization stay valid."""
    card = build_agent_card("https://public.example")
    payload = json_format.MessageToDict(card)
    round_tripped = AgentCard()
    json_format.ParseDict(payload, round_tripped)

    assert payload["securitySchemes"]["bearer"]["httpAuthSecurityScheme"] == {
        "scheme": "Bearer",
        "bearerFormat": "API key",
    }
    assert payload["securityRequirements"] == [
        {"schemes": {"bearer": {"list": ["agents"]}}}
    ]
    assert round_tripped == card
