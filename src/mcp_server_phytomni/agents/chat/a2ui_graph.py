# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Node functions for the Chat A2UI confirm interrupt graph.

The dedicated A2UI graph pauses at a confirm surface before the main
LLM call. On resume, accepted decisions flow through the shared
``generate_node`` / ``follow_up_node`` pair; rejected decisions settle
a short cancel response without a second confirm interrupt.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.types import interrupt

from ..shared.a2ui import (
    ConfirmProps,
    build_a2ui_value,
    mint_surface_id,
)
from .state import ChatState

_CANCEL_MESSAGE = "Cancelled — no further action taken."


class ChatA2uiState(ChatState, total=False):
    """Internal state for the Chat A2UI confirm workflow.

    Extends :class:`ChatState` with the confirm-surface draft, the
    human decision returned by ``interrupt()`` on resume, and an
    optional cancel message for the rejected branch.
    """

    a2ui_surface: dict[str, Any] | None
    a2ui_decision: dict[str, Any] | None
    cancel_message: str | None


class ChatA2uiOutput(TypedDict, total=False):
    """Public output contract for the Chat A2UI confirm workflow.

    Carries the terminal ``response`` plus the minted confirm surface so
    callers can validate ``surface_id`` against the paused draft.
    """

    response: dict[str, Any] | None
    a2ui_surface: dict[str, Any] | None


async def a2ui_prepare_surface_node(
    state: ChatA2uiState,
) -> dict[str, Any]:
    """Mint the confirm surface once before the interrupt node.

    LangGraph replays the interrupt node from the top on resume; surface
    minting lives here so the same ``surface_id`` survives replay.

    Args:
        state: Current workflow state; reads ``user_query`` and
            ``a2ui_surface``.

    Returns:
        State delta with the downlink surface, or empty when already set.
    """
    if state.get("a2ui_surface"):
        return {}
    surface_id = mint_surface_id()
    value = build_a2ui_value(
        surface_id=surface_id,
        widget="confirm",
        props=ConfirmProps(
            title="Confirm",
            body=state["user_query"][:500],
        ),
    )
    return {"a2ui_surface": value}


async def a2ui_confirm_node(state: ChatA2uiState) -> dict[str, Any]:
    """Pause for human confirmation before the main LLM call.

    Reads the pre-minted surface from state and calls ``interrupt()``
    with the draft payload. On resume LangGraph replays this node from
    the top and ``interrupt()`` returns the decision the adapter
    supplied without reminting ``surface_id``.

    Args:
        state: Current workflow state; reads ``a2ui_surface``.

    Returns:
        State delta recording the human decision.
    """
    surface = state.get("a2ui_surface")
    if surface is None:
        msg = "a2ui_surface missing before confirm interrupt"
        raise RuntimeError(msg)
    decision = interrupt({"a2ui": surface})
    return {"a2ui_decision": decision}


def route_after_a2ui_confirm(
    state: ChatA2uiState,
) -> Literal["generate_node", "a2ui_cancel_node"]:
    """Route accepted confirms to generate, rejected confirms to cancel.

    Args:
        state: Current workflow state; reads ``a2ui_decision``.

    Returns:
        ``"generate_node"`` when the human accepted, else
        ``"a2ui_cancel_node"``.
    """
    decision = state.get("a2ui_decision") or {}
    if decision.get("accepted"):
        return "generate_node"
    return "a2ui_cancel_node"


async def a2ui_cancel_node(state: ChatA2uiState) -> dict[str, Any]:
    """Settle a short cancel response without calling the main LLM.

    Args:
        state: Current workflow state (unused).

    Returns:
        State delta with a terminal ``response`` carrying the cancel
        message.
    """
    del state
    return {
        "cancel_message": _CANCEL_MESSAGE,
        "response": {
            "choices": [
                {
                    "message": {
                        "content": _CANCEL_MESSAGE,
                        "follow_up_questions": [],
                    }
                }
            ]
        },
    }
