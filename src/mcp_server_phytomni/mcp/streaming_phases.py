# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Whitelist mapping graph node names to semantic streaming phases.

Only reduce / post nodes appear so the fan-out worker and dispatch
nodes are folded away; the seam further dedups repeats. Keeping the
table here (not in the agent graphs) decouples the public StepStarted
contract from internal graph topology so a node rename never breaks the
wire contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from typing import Any, TypedDict, cast

from ..runtime.cleanup import run_bounded_cleanup
from .schemas import PhytomniAgents

GRAPH_PROGRESS_TOOLS = frozenset(
    {
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        PhytomniAgents.REVIEW_AGENT.value,
        PhytomniAgents.DATA_AGENT.value,
        PhytomniAgents.BRIEF_GENE_AGENT.value,
    }
)


class PrivateStreamKwargs(TypedDict, total=False):
    """Private streaming inputs excluded from public MCP schemas."""

    conversation_messages: Sequence[Mapping[str, str]]
    private_agent_state: Mapping[str, Any] | None


class StreamRunMeta(TypedDict):
    """Run identity carried through graph-streaming calls."""

    run_id: str
    dialogue_id: str | None


async def close_async_iterator(stream: Any) -> None:
    """Propagate consumer shutdown through one nested async iterator."""
    closer = getattr(stream, "aclose", None)
    if callable(closer):
        await run_bounded_cleanup(
            cast(Callable[[], Awaitable[None]], closer)(),
            operation="iterator_close",
        )


async def iterate_owned(
    stream: AsyncIterator[Any],
) -> AsyncIterator[Any]:
    """Cancel and await an in-flight next call before consumer shutdown."""
    while True:
        next_item = asyncio.ensure_future(anext(stream))
        try:
            # Keep cancellation and exception observation with this owner.
            await asyncio.wait({next_item})
            item = next_item.result()
        except StopAsyncIteration:
            return
        except asyncio.CancelledError:
            next_item.cancel()
            await run_bounded_cleanup(
                next_item,
                operation="iterator_next",
                cancelled_is_success=True,
            )
            raise
        yield item


_PHASE_MAP: dict[str, dict[str, str]] = {
    "KnowledgeAgent": {
        "retrieve_node": "retrieving",
        "generate_post_node": "generating",
    },
    "ReviewAgent": {
        "plan_query_post_node": "planning",
        "retrieve_reduce_node": "retrieving",
        "draft_reduce_node": "drafting",
        "revised_reduce_node": "revising",
        "summary_post_node": "generating",
    },
    "DataAgent": {
        "retrieve_post_node": "retrieving",
        "rewrite_post_node": "rewriting",
        "search_node": "querying",
    },
    "BriefGeneAgent": {
        "fetch_annotation_node": "annotating",
        "retrieve_reduce_node": "retrieving",
        "section_discovery_node": "analyzing",
        "section_cloning_node": "analyzing",
        "section_functional_node": "analyzing",
        "section_application_node": "analyzing",
        "render_node": "generating",
    },
}


def phase_for(agent: str, node: str) -> str | None:
    """Return the semantic phase for one node, or None to drop it."""
    return _PHASE_MAP.get(agent, {}).get(node)
