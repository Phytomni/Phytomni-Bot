# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the HTTP resolve_gene_id flag on BriefGene paths.

Covers /v1/chat/completions and /v1/agents/brief_gene/runs: flag-on
rewriting, flag-off passthrough, non-BriefGene rejection, missing
user_query rejection, and resolver-error 400 mapping.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.brief_gene.resolve_query import (
    BriefGeneIdCandidate,
    BriefGeneResolveError,
    BriefGeneResolveResult,
)
from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def _resolved(gene_id: str, raw: str) -> BriefGeneResolveResult:
    """Return a canned resolver result for a single-candidate case."""
    return BriefGeneResolveResult(
        gene_id=gene_id,
        raw_query=raw,
        candidates=[BriefGeneIdCandidate(gene_id=gene_id, confidence=1.0)],
    )


def _stub_brief_gene_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace BriefGeneAgent handler with a canned dict response."""

    async def fake(args: Any) -> dict[str, Any]:
        """Capture user_query and return a fixed completion payload."""
        captured["user_query"] = args.user_query
        return {
            "id": "chatcmpl-canned",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "canned brief gene annotation",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        fake,
    )


def _stub_chat_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace ChatAgent handler so resolver-flag-misuse path returns 200 only
    when resolver is bypassed (we expect a 400 BEFORE reaching this stub)."""

    async def fake(args: Any) -> dict[str, Any]:
        """Capture user_query in case the handler is unexpectedly reached."""
        captured["user_query"] = args.user_query
        return {
            "id": "chatcmpl-chat",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "chat answer"},
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS, server.PhytomniAgents.CHAT_AGENT.value, fake
    )


async def test_chat_resolves_when_flag_true_for_brief_gene(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag=true rewrites user_query and surfaces resolver metadata."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Capture the raw query and return a fixed canonical id."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("AT5G42800", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    raw_query = "What does AT5G42800 do in Arabidopsis?"
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        messages=[{"role": "user", "content": raw_query}],
        resolve_gene_id=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["user_query"] == "AT5G42800"
    assert resolver_calls == [raw_query]
    metadata = body.get("metadata") or {}
    assert metadata.get("original_query") == raw_query
    assert metadata.get("resolved_gene_id") == "AT5G42800"
    assert metadata.get("resolve_gene_id") is True


async def test_chat_skips_resolver_when_flag_false(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag=false leaves user_query untouched, emits no resolver metadata."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run when flag is false."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="AT5G42800",
        resolve_gene_id=False,
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["user_query"] == "AT5G42800"
    assert resolver_calls == []
    metadata = body.get("metadata") or {}
    assert "resolved_gene_id" not in metadata


async def test_chat_skips_resolver_when_flag_missing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting the flag entirely matches the flag-false behavior."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run when flag is missing."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="AT5G42800",
    )

    assert response.status_code == 200
    assert captured["user_query"] == "AT5G42800"
    assert resolver_calls == []


async def test_chat_rejects_resolve_flag_on_non_brief_gene_model(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag=true on phyto-chat returns 400 and never reaches the resolver."""
    chat_captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_chat_handler(monkeypatch, chat_captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run for non-BriefGene tools."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-chat",
        content="explain photosynthesis",
        resolve_gene_id=True,
    )

    assert response.status_code == 400
    body = response.json()
    assert "BriefGene" in body["error"]["message"]
    assert resolver_calls == []
    assert "user_query" not in chat_captured


async def test_chat_resolver_failure_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BriefGeneResolveError surfaces as 400 with the resolver's reason."""
    captured: dict[str, Any] = {}
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Raise the expected resolver-failure exception."""
        del raw_query, brief_config, sensitive_config
        raise BriefGeneResolveError("no valid candidate")

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="ambiguous text",
        resolve_gene_id=True,
    )

    assert response.status_code == 400
    body = response.json()
    assert "no valid candidate" in body["error"]["message"]
    assert "user_query" not in captured
