# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Observable V0 Expert selection isolated from the HTTP application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from ..agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    ExpertRoutingDeclinedError,
    ToolSelection,
)
from ..agents.expert.routing_observability import (
    ExpertRouteOutcome,
    ExpertRoutePath,
    record_expert_route_outcome,
)
from .expert_routing_errors import (
    expert_routing_contract_error,
    expert_routing_provider_error,
)
from .schemas import ExpertQueryRequest

type ExpertSelector = Callable[..., Awaitable[ToolSelection | None]]


def _record_v0_route_outcome(
    outcome: ExpertRouteOutcome,
    *,
    payload: ExpertQueryRequest,
    http_status: int,
    error: BaseException | None = None,
) -> None:
    """Log one V0 selection-stage outcome without the query body."""
    record_expert_route_outcome(
        outcome,
        path=ExpertRoutePath.V0,
        forced=payload.forced_tool is not None,
        error_class=None if error is None else type(error).__name__,
        http_status=http_status,
    )


async def select_expert_routing(
    payload: ExpertQueryRequest,
    selector: ExpertSelector,
    tool_to_slug: Mapping[str, str],
) -> tuple[ToolSelection, str]:
    """Run the canonical Expert selector and resolve its public slug."""
    try:
        selection = await selector(
            payload.user_query,
            payload.history,
            allowed_tools=payload.allowed_tools,
            forced_tool=payload.forced_tool,
        )
    except ExpertRoutingDeclinedError as exc:
        if payload.forced_tool is not None or "ChatAgent" not in (
            payload.allowed_tools
        ):
            _record_v0_route_outcome(
                ExpertRouteOutcome.DECLINED_NO_FALLBACK,
                payload=payload,
                http_status=502,
                error=exc,
            )
            raise expert_routing_contract_error() from exc
        _record_v0_route_outcome(
            ExpertRouteOutcome.DECLINED_CHAT_FALLBACK,
            payload=payload,
            http_status=200,
            error=exc,
        )
        selection = ToolSelection(
            tool_name="ChatAgent",
            arguments={"user_query": payload.user_query},
        )
    except ExpertRoutingContractError as exc:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
            error=exc,
        )
        raise expert_routing_contract_error() from exc
    except ExpertProviderError as exc:
        timed_out = isinstance(exc, ExpertProviderTimeoutError)
        _record_v0_route_outcome(
            (
                ExpertRouteOutcome.PROVIDER_TIMEOUT
                if timed_out
                else ExpertRouteOutcome.PROVIDER_ERROR
            ),
            payload=payload,
            http_status=504 if timed_out else 502,
            error=exc,
        )
        raise expert_routing_provider_error(exc) from exc
    if selection is None:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
        )
        raise expert_routing_contract_error()
    slug = tool_to_slug.get(selection.tool_name)
    if slug is None:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
        )
        raise expert_routing_contract_error()
    return selection, slug


__all__ = ["ExpertSelector", "select_expert_routing"]
