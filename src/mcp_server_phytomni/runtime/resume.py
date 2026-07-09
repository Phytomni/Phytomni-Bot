# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Protocol-agnostic resume kernel for human-in-the-loop graphs.

:func:`aresume_graph` drives a paused LangGraph app from its
``interrupt()`` point to the next interrupt or terminal state.  It
knows nothing about HTTP or MCP; both adapters translate their
protocol inputs into a :data:`ResumePayload`, mirroring the
``invoke_tool_enveloped`` single-core discipline.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, TypedDict

from langgraph.types import Command
from mcp.types import ClientCapabilities, ElicitationCapability

from .langgraph_runner import build_runnable_config

logger = logging.getLogger(__name__)

# The plain approval decision shape both adapters translate into.
ResumePayload = dict[str, Any]

_INTERRUPT_KEY = "__interrupt__"


class InterruptInfo(TypedDict):
    """The pause-point payload surfaced to an adapter.

    Attributes:
        thread_id: The graph thread id to resume against.
        draft: The value passed to ``interrupt(value)``
            inside the node.
    """

    thread_id: str
    draft: Any


class NoCheckpointError(RuntimeError):
    """Raised when resuming a thread that has no stored pause point."""


def detect_interrupt(
    final_state: Mapping[str, Any],
    thread_id: str = "",
) -> InterruptInfo | None:
    """Return interrupt info when a graph run paused, else None.

    Args:
        final_state: The dict returned by ``ainvoke`` / ``astream``.
        thread_id: The thread id to echo back into the info.

    Returns:
        ``InterruptInfo`` carrying the first interrupt's ``.value``
        as ``draft`` when the run paused, otherwise ``None``.
    """
    raw = final_state.get(_INTERRUPT_KEY)
    if not raw:
        return None
    first = raw[0]
    draft = getattr(first, "value", first)
    return InterruptInfo(thread_id=thread_id, draft=draft)


async def elicit_review_decision(
    session: Any,
    draft: Any,
) -> ResumePayload:
    """Collect a ReviewAgent approval decision through MCP elicitation.

    Clients that do not advertise elicitation support are auto-approved so
    legacy one-shot stdio calls keep their existing terminal behavior.
    """
    capable = session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    )
    if not capable:
        return {"approved": True, "edits": None}
    result = await session.elicit(
        message=(
            "Review the drafted summary and approve or reject.\n\n"
            f"Draft:\n{draft}"
        ),
        requestedSchema={
            "type": "object",
            "properties": {
                "approved": {"type": "boolean"},
                "edits": {"type": "string"},
            },
            "required": ["approved"],
        },
    )
    if getattr(result, "action", None) == "accept":
        content = getattr(result, "content", None) or {}
        return {"approved": True, "edits": content.get("edits")}
    return {"approved": False, "edits": None}


async def aresume_graph(
    app: Any,
    thread_id: str,
    resume_payload: ResumePayload,
) -> dict[str, Any]:
    """Resume a paused graph from its interrupt point.

    Args:
        app: The compiled LangGraph application (same instance or
            config used for the first run; the checkpointer holds
            the state).
        thread_id: The thread id whose pause point to resume.
        resume_payload: The plain decision payload the interrupted
            node receives as ``interrupt()``'s return value.

    Returns:
        The next final state, which may itself carry a new
        ``__interrupt__`` (a further approval round).

    Raises:
        NoCheckpointError: When the thread has no stored pause point.
    """
    config = build_runnable_config(thread_id)
    checkpointer = getattr(app, "checkpointer", None)
    if checkpointer is not None:
        checkpoint = await checkpointer.aget(config)
        if checkpoint is None:
            raise NoCheckpointError(
                f"No checkpoint found for thread " f"{thread_id!r}"
            )
    return await app.ainvoke(
        Command(resume=resume_payload),
        config=config,
    )
