# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Node functions for the Chat A2UI confirm/form/choice interrupt graph.

The dedicated A2UI graph pauses at a confirm/form/choice surface
before the main LLM call. On resume, accepted/submitted decisions
flow through ``a2ui_apply_decision_node`` into ``generate_node`` /
``follow_up_node``; rejected/cancelled decisions settle a short
cancel response without a second interrupt. After work,
``route_after_a2ui_turn`` may remint while under the N=2 round cap.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.types import interrupt

from ...common.responses import message_content
from ..shared.a2ui import author_a2ui_surface
from ..shared.a2ui.loop import (
    clear_a2ui_for_reenter,
    next_a2ui_round,
    should_reenter_a2ui,
)
from .state import ChatState

_CANCEL_MESSAGE = "Cancelled — no further action taken."


class ChatA2uiState(ChatState, total=False):
    """Internal state for the Chat A2UI confirm workflow.

    Extends :class:`ChatState` with the confirm/form/choice surface
    draft, the human decision returned by ``interrupt()`` on resume,
    the injected form/choice answer summary, an optional cancel
    message for the rejected/cancelled branch, and the bounded
    multi-turn ``a2ui_round`` counter.
    """

    a2ui_surface: dict[str, Any] | None
    a2ui_decision: dict[str, Any] | None
    a2ui_user_input: str | None
    cancel_message: str | None
    a2ui_round: int


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
    """Mint the confirm/form/choice surface once before the interrupt.

    LangGraph replays the interrupt node from the top on resume; surface
    minting lives here so the same ``surface_id`` survives replay. Props
    come from :func:`author_a2ui_surface` (domain → LLM → thin).

    Args:
        state: Current workflow state; reads ``user_query`` and
            ``a2ui_surface``.

    Returns:
        State delta with the downlink surface, or empty when already set.
    """
    if state.get("a2ui_surface"):
        return {}
    value = await author_a2ui_surface(
        {"text": state["user_query"], "agent": "chat"},
    )
    return {
        "a2ui_surface": value,
        "a2ui_round": next_a2ui_round(state.get("a2ui_round")),
    }


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
) -> Literal["a2ui_apply_decision_node", "a2ui_cancel_node"]:
    """Route accepted/submitted decisions to apply, else to cancel.

    Form and choice surfaces cancel only on an explicit ``cancelled``
    flag (any other decision, i.e. a submit, proceeds to apply).
    Confirm surfaces cancel unless ``accepted`` is truthy.

    Args:
        state: Current workflow state; reads ``a2ui_decision`` and
            ``a2ui_surface``.

    Returns:
        ``"a2ui_apply_decision_node"`` when the human proceeded, else
        ``"a2ui_cancel_node"``.
    """
    decision = state.get("a2ui_decision") or {}
    widget = decision.get("widget") or (
        (state.get("a2ui_surface") or {}).get("widget")
    )
    if widget in ("form", "choice"):
        if decision.get("cancelled") is True:
            return "a2ui_cancel_node"
        return "a2ui_apply_decision_node"
    if decision.get("accepted"):
        return "a2ui_apply_decision_node"
    return "a2ui_cancel_node"


async def a2ui_apply_decision_node(
    state: ChatA2uiState,
) -> dict[str, Any]:
    """Inject form/choice answers into user_query before generate.

    Confirm accept is a no-op on ``user_query``: the human simply
    approved the original request as-is.

    Args:
        state: Current workflow state; reads ``a2ui_decision``,
            ``a2ui_surface``, and ``user_query``.

    Returns:
        State delta prepending the injected summary to ``user_query``
        for form/choice, or empty for confirm.
    """
    decision = state.get("a2ui_decision") or {}
    widget = decision.get("widget") or (
        (state.get("a2ui_surface") or {}).get("widget")
    )
    if widget == "form":
        fields = decision.get("fields") or {}
        summary = "User form input: " + ", ".join(
            f"{key}={fields[key]}" for key in sorted(fields)
        )
    elif widget == "choice":
        summary = f"User selected: {decision.get('selected')}"
    else:
        return {}
    original = state["user_query"]
    return {
        "a2ui_user_input": summary,
        "user_query": f"{summary}\n\n{original}",
    }


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


async def a2ui_after_work_node(state: ChatA2uiState) -> dict[str, Any]:
    """No-op join after generate/follow_up before the re-enter router.

    Args:
        state: Current workflow state (unused).

    Returns:
        Empty state delta.
    """
    del state
    return {}


async def a2ui_reenter_node(state: ChatA2uiState) -> dict[str, Any]:
    """Clear surface and decision so prepare can mint a fresh round.

    Args:
        state: Current workflow state (unused).

    Returns:
        State delta nulling ``a2ui_surface`` and ``a2ui_decision``.
    """
    del state
    return clear_a2ui_for_reenter()


def route_after_a2ui_turn(
    state: ChatA2uiState,
) -> Literal["a2ui_reenter_node", "__end__"]:
    """Route to remint when assistant text cues another A2UI round.

    Args:
        state: Current workflow state; reads ``response`` and
            ``a2ui_round``.

    Returns:
        ``"a2ui_reenter_node"`` when another pause is allowed, else
        ``"__end__"``.
    """
    text = message_content(state.get("response"))
    round_ = state.get("a2ui_round") or 0
    if should_reenter_a2ui(text=text, a2ui_round=round_):
        return "a2ui_reenter_node"
    return "__end__"
