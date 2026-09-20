# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded Runtime instrumentation for Review draft dimensions."""

from __future__ import annotations

from typing import Any

from ...runtime.langgraph_runner import invoke_graph
from ...runtime.operation_instrumentation_v2 import (
    instrument_operation_invocation,
)


async def invoke_draft_dimension(
    chat_app: Any,
    chat_payload: dict[str, Any] | None,
    task_index: int,
    dimension_total: int,
) -> dict[str, Any]:
    """Invoke one Review draft dimension with stable ordinal metadata."""
    if chat_payload is None:
        raise ValueError("draft_chat_payload_missing")
    return await instrument_operation_invocation(
        "review.draft_dimension",
        lambda: invoke_graph(chat_app, chat_payload),
        detail={
            "ordinal": task_index + 1,
            "total": max(1, dimension_total),
        },
    )


__all__ = ["invoke_draft_dimension"]
