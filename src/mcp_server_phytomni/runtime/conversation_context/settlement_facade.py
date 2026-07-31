# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility facade for the Review settlement route call shape."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .store import StoredTurn


@dataclass(frozen=True, slots=True)
class _ReviewSettlementRequest:
    """Validated arguments for restart-safe Review acknowledgment."""

    key: tuple[str, str]
    accepted: bool
    staged_turn: StoredTurn | None
    expected_ledger_version: str | None
    mutation_lock_held: bool


_SIGNATURE = inspect.Signature(
    parameters=(
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter(
            "conversation_key",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation="str",
        ),
        inspect.Parameter(
            "turn_id",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation="str",
        ),
        inspect.Parameter(
            "accepted", inspect.Parameter.KEYWORD_ONLY, annotation="bool"
        ),
        inspect.Parameter(
            "staged_turn",
            inspect.Parameter.KEYWORD_ONLY,
            annotation="StoredTurn | None",
            default=None,
        ),
        inspect.Parameter(
            "expected_ledger_version",
            inspect.Parameter.KEYWORD_ONLY,
            annotation="str | None",
            default=None,
        ),
        inspect.Parameter(
            "mutation_lock_held",
            inspect.Parameter.KEYWORD_ONLY,
            annotation="bool",
            default=False,
        ),
    ),
    return_annotation="bool",
)


def _facade(
    executor_type: type[Any],
) -> Callable[..., Awaitable[bool]]:
    """Build a strict facade that retains the historical public signature."""

    async def invoke(self: Any, *args: Any, **kwargs: Any) -> bool:
        """Validate the compatibility call before its request seam."""
        bound = _SIGNATURE.bind(self, *args, **kwargs)
        bound.apply_defaults()
        request = _ReviewSettlementRequest(
            key=(
                bound.arguments["conversation_key"],
                bound.arguments["turn_id"],
            ),
            accepted=bound.arguments["accepted"],
            staged_turn=bound.arguments["staged_turn"],
            expected_ledger_version=bound.arguments["expected_ledger_version"],
            mutation_lock_held=bound.arguments["mutation_lock_held"],
        )
        implementation = getattr(
            self, "_acknowledge_review_settlement_for_turn"
        )
        return await implementation(request)

    metadata = (
        ("__signature__", _SIGNATURE),
        (
            "__annotations__",
            {
                "conversation_key": "str",
                "turn_id": "str",
                "accepted": "bool",
                "staged_turn": "StoredTurn | None",
                "expected_ledger_version": "str | None",
                "mutation_lock_held": "bool",
                "return": "bool",
            },
        ),
        ("__name__", "acknowledge_review_settlement_for_turn"),
        (
            "__qualname__",
            f"{executor_type.__name__}.acknowledge_review_settlement_for_turn",
        ),
        ("__module__", executor_type.__module__),
    )
    for name, value in metadata:
        setattr(invoke, name, value)
    return invoke


def install_review_settlement_facade(executor_type: type[Any]) -> None:
    """Install the stable public Review settlement adapter method."""
    setattr(
        executor_type,
        "acknowledge_review_settlement_for_turn",
        _facade(executor_type),
    )


__all__ = [
    "ReviewSettlementForTurnRequest",
    "install_review_settlement_facade",
]

ReviewSettlementForTurnRequest = _ReviewSettlementRequest
