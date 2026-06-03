# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Relay-mode platform adapter tests.

Pins that in customer relay mode each platform HTTP boundary routes
through the relay routes (via ``current_relay_client``) instead of the
operator endpoint, preserving the request body shape and response
parsing. Private boundary helpers are imported by name so the tests
exercise them without a protected-access access expression.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.knowledge import retrieval
from mcp_server_phytomni.agents.knowledge.retrieval import (
    _rerank_batch,
    _retrieve_scope_docs,
)

pytestmark = pytest.mark.agent

# The boundary helpers take an ``AsyncClient`` they ignore in relay mode
# (the relay path uses ``current_relay_client``); ``Any`` lets the tests
# pass a placeholder without tripping the ``AsyncClient`` parameter type.
_UNUSED_CLIENT: Any = None


class _FakeRelay:
    """Records relay calls and replays a canned JSON response."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post_json(
        self, relay_path: str, *, json_body: Any, message: str
    ) -> Any:
        """Record a relay POST and return the canned response."""
        self.calls.append(
            {"path": relay_path, "body": json_body, "message": message}
        )
        return self.response


def _patch_relay(monkeypatch, module: Any, response: Any) -> _FakeRelay:
    """Patch ``current_relay_client`` in ``module`` to a recording fake."""
    relay = _FakeRelay(response)
    monkeypatch.setattr(module, "current_relay_client", lambda: relay)
    return relay


async def test_retrieve_scope_docs_routes_through_relay(monkeypatch):
    """Relay mode posts the retrieve body to /v1/relay/retrieve/search."""
    _retrieve_scope_docs.cache_clear()
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, retrieval, {"doc_list": [{"id": "d1"}]})

    docs = await _retrieve_scope_docs(
        _UNUSED_CLIENT,
        user_query="leaf growth",
        retrieve_url="https://operator.invalid/search",
        repo_id="repo-1",
        scope="document",
        page_num=1,
        page_size=3,
        filter_string=None,
        extra_repo_ids=(),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(503,),
    )

    assert docs == [{"id": "d1"}]
    assert relay.calls[0]["path"] == "retrieve/search"
    assert relay.calls[0]["body"]["repo_id"] == "repo-1"
    assert relay.calls[0]["body"]["content"] == "leaf growth"


async def test_rerank_batch_routes_through_relay(monkeypatch):
    """Relay mode posts the rerank body to /v1/relay/rerank/rank."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(
        monkeypatch, retrieval, {"rank_result": [{"id": "r1"}]}
    )

    ranked = await _rerank_batch(
        _UNUSED_CLIENT,
        user_query="leaf growth",
        docs_batch=[{"id": "d1"}],
        rerank_url="https://operator.invalid/rerank",
        top_n=3,
        timeout=1.0,
        max_retries=0,
        retriable_codes=(503,),
    )

    assert ranked == [{"id": "r1"}]
    assert relay.calls[0]["path"] == "rerank/rank"
    assert relay.calls[0]["body"]["query"] == "leaf growth"
    assert relay.calls[0]["body"]["docs"] == [{"id": "d1"}]
