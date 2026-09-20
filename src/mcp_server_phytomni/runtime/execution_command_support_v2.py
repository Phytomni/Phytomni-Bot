# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared command identity and supervisor settlement primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .execution_runtime_contracts import TerminalSettlementAuthority


@dataclass(frozen=True, slots=True)
class CommandIdentity:
    """Validated owner-bound agent and argument payload."""

    agent: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ValidatedCommand:
    """Typed invocation payload derived from a durable command."""

    tool: str
    agent: str
    arguments: dict[str, Any]
    fingerprint_version: int
    fingerprint: str


def command_identity(
    command: Mapping[str, object],
    *,
    owner_ref: str,
    execution_id: str,
) -> CommandIdentity | None:
    """Return typed command identity when its ownership fence matches."""
    agent = command.get("agent")
    arguments = command.get("arguments")
    if command.get("owner_ref") != owner_ref:
        return None
    if command.get("execution_id") != execution_id:
        return None
    if not isinstance(agent, str) or not isinstance(arguments, dict):
        return None
    return CommandIdentity(agent=agent, arguments=arguments)


def conversation_identity(
    command: Mapping[str, object],
) -> tuple[str | None, str | None]:
    """Extract a normalized conversation and turn identity when present."""
    conversation = _conversation_payload(command)
    if conversation is None:
        return None, None
    key = conversation.get("conversation_key")
    turn = conversation.get("turn_id")
    return (
        key if isinstance(key, str) and key else None,
        str(turn) if isinstance(turn, (str, int)) else None,
    )


def matches_conversation_turn(
    command: Mapping[str, object],
    *,
    conversation_key: str,
    turn_id: str,
) -> bool:
    """Match the exact persisted turn fence used by safe redispatch."""
    conversation = _conversation_payload(command)
    return (
        conversation is not None
        and conversation.get("conversation_key") == conversation_key
        and str(conversation.get("turn_id")) == turn_id
    )


def _conversation_payload(
    command: Mapping[str, object],
) -> dict[object, object] | None:
    arguments = command.get("arguments")
    conversation = (
        arguments.get("__conversation")
        if isinstance(arguments, dict)
        else None
    )
    return conversation if isinstance(conversation, dict) else None


def projection_has_terminal(value: object) -> bool:
    """Return whether serialized projection state contains a terminal."""
    if not isinstance(value, str):
        return False
    try:
        projection = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(projection, dict) and projection.get("terminal") is not None
    )


def supervisor_settlement_authority(
    *,
    owner_ref: str,
    execution_id: str,
    expected_revision: int,
    issued_at: datetime,
) -> TerminalSettlementAuthority:
    """Build the canonical supervisor authority for terminal settlement."""
    return TerminalSettlementAuthority(
        owner_ref=owner_ref,
        execution_id=execution_id,
        expected_revision=expected_revision,
        actor="supervisor",
        issued_at=issued_at,
    )


__all__ = [
    "CommandIdentity",
    "ValidatedCommand",
    "command_identity",
    "conversation_identity",
    "matches_conversation_turn",
    "projection_has_terminal",
    "supervisor_settlement_authority",
]
