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

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from tests.support.resolver_fakes import (
    assert_invalid_argument_response,
    post_native_run,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.brief_gene.resolve_query import (
    BriefGeneIdCandidate,
    BriefGeneResolveError,
    BriefGeneResolveResult,
)
from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def _resolved(
    gene_id: str, raw: str, species_code: str = "osa"
) -> BriefGeneResolveResult:
    """Return a canned resolver result for a single-candidate case.

    ``species_code`` defaults to ``"osa"`` (rice) for fixture brevity;
    tests that wire-shape-check the metadata block can override per
    case when the expected value differs from the default.
    """
    return BriefGeneResolveResult(
        gene_id=gene_id,
        raw_query=raw,
        species_code=species_code,
        candidates=[
            BriefGeneIdCandidate(
                gene_id=gene_id, confidence=1.0, species_code=species_code
            )
        ],
    )


def _register_capture(
    monkeypatch: pytest.MonkeyPatch,
    agent_enum_value: str,
    captured: dict[str, Any],
    answer: str,
) -> None:
    """Replace one tool handler with a minimal capturing stub.

    Uses a tabular answer/doc_list shape (not the full OpenAI envelope)
    so this helper does not share a long line block with other test
    files that already stub the OpenAI completion structure.
    """

    async def fake(args: Any) -> dict[str, Any]:
        """Capture user_query and return a minimal payload."""
        captured["user_query"] = args.user_query
        return {"answer": answer, "doc_list": []}

    monkeypatch.setitem(server.TOOL_HANDLERS, agent_enum_value, fake)


def _stub_brief_gene_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace BriefGeneAgent handler with a tabular-shaped stub."""
    _register_capture(
        monkeypatch,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        captured,
        "brief gene annotation",
    )


def _stub_chat_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace ChatAgent handler so non-BriefGene rejection cases stay sealed.

    Resolver-flag-misuse paths must 400 before this stub runs, so the
    captured dict should stay empty in those cases.
    """
    _register_capture(
        monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
        captured,
        "chat answer",
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
    metadata = body["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == raw_query
    assert metadata.get("resolved_gene_id") == "AT5G42800"
    assert metadata.get("resolved_species_code") == "osa"
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
    assert not resolver_calls
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
    assert not resolver_calls


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

    assert_invalid_argument_response(response)
    assert not resolver_calls
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

    assert_invalid_argument_response(response)
    assert "user_query" not in captured


async def _post_brief_gene_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """POST one native brief_gene run via the generic agent-run helper."""
    return await post_native_run(
        api_client, issued_api_key, "brief_gene", arguments
    )


async def test_native_runs_resolves_when_flag_true_for_brief_gene(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true rewrites user_query, pops the key, and patches metadata."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Return a fixed resolution for the route flow."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("Os01g0177400", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await _post_brief_gene_run(
        api_client,
        issued_api_key,
        {
            "user_query": "rice TPR6 function",
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["user_query"] == "Os01g0177400"
    assert resolver_calls == ["rice TPR6 function"]
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "rice TPR6 function"
    assert metadata.get("resolved_gene_id") == "Os01g0177400"
    assert metadata.get("resolved_species_code") == "osa"
    assert metadata.get("resolve_gene_id") is True


async def test_native_runs_skips_resolver_when_flag_false_or_missing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=false leaves user_query as-is, and the key is still popped."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run when the flag is false or missing."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await _post_brief_gene_run(
        api_client,
        issued_api_key,
        {"user_query": "AT5G42800", "resolve_gene_id": False},
    )

    assert response.status_code == 200
    assert captured["user_query"] == "AT5G42800"
    assert not resolver_calls
    metadata = response.json()["result"].get("metadata") or {}
    assert "resolved_gene_id" not in metadata


async def test_native_runs_rejects_resolve_flag_on_non_brief_gene_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true on /v1/agents/chat/runs returns 400, resolver never runs."""
    del tasks_db_path
    chat_captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_chat_handler(monkeypatch, chat_captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run for non-BriefGene agents."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await post_native_run(
        api_client,
        issued_api_key,
        "chat",
        {
            "user_query": "explain photosynthesis",
            "resolve_gene_id": True,
        },
    )

    assert_invalid_argument_response(response)
    assert not resolver_calls
    assert "user_query" not in chat_captured


async def test_native_runs_rejects_missing_user_query(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true without user_query is 400 before the resolver is reached."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Should not run when user_query is missing or blank."""
        del brief_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await _post_brief_gene_run(
        api_client,
        issued_api_key,
        {"resolve_gene_id": True},
    )

    assert_invalid_argument_response(response)
    assert not resolver_calls


async def test_native_runs_resolver_failure_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Native path also maps BriefGeneResolveError to HTTP 400."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    _stub_brief_gene_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, brief_config: Any, sensitive_config: Any
    ) -> BriefGeneResolveResult:
        """Raise the expected resolver-failure exception."""
        del raw_query, brief_config, sensitive_config
        raise BriefGeneResolveError("non-parseable LLM output: bad json")

    monkeypatch.setattr(api_app, "resolve_brief_gene_user_query", fake_resolve)

    response = await _post_brief_gene_run(
        api_client,
        issued_api_key,
        {"user_query": "ambiguous", "resolve_gene_id": True},
    )

    assert_invalid_argument_response(response)
    assert "user_query" not in captured
