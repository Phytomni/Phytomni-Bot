# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Build the public A2A v1 Agent Card without mounting any route."""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)

from ... import __version__
from .catalog import build_a2a_skills

__all__ = ["build_agent_card"]


def build_agent_card(public_base_url: str) -> AgentCard:
    """Build a discovery card for the feature-flagged A2A v1 surface.

    Args:
        public_base_url: Validated public HTTP(S) URL from ``ApiConfig``.

    Returns:
        A fresh protobuf ``AgentCard`` with one JSON-RPC interface. Streaming,
        push notifications, and authenticated extended cards remain disabled
        until their later phases are implemented.
    """
    interface_url = f"{public_base_url.rstrip('/')}/a2a"
    card = AgentCard(
        name="Phytomni",
        description="Plant-science research agents exposed by Phytomni.",
        supported_interfaces=[
            AgentInterface(
                url=interface_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        version=__version__,
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=build_a2a_skills(),
    )
    card.security_schemes["bearer"].CopyFrom(
        SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                scheme="Bearer",
                bearer_format="API key",
            )
        )
    )
    card.security_requirements.add().schemes["bearer"].list.append("agents")
    return card
