# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Concurrent regression smoke for five chat-like agents over HTTP.

Boots ``phytomni-api`` as a uvicorn subprocess via
``helpers.api_server.boot_phytomni_api``, mints a per-session
throwaway API key, then drives all five chat-like agents in
parallel through ``asyncio.gather`` over real HTTP. Each branch
validates with its agent-specific assertion from
``helpers/assertions.py`` (shared with the MCP stdio counterpart).

Route split mirrors what an external integrator actually sees:

* ChatAgent / KnowledgeAgent / ReviewAgent / BriefGeneAgent go
  through ``POST /v1/chat/completions`` (the OpenAI-compatible
  route that the four ``phyto-*`` models in
  ``api/openai_mapping.MODEL_TO_TOOL`` resolve to).
* DataAgent goes through ``POST /v1/agents/data/runs`` (the
  uniform agent.run-envelope route) because ``DataAgent`` is not
  in ``MODEL_TO_TOOL``.

Intended use: a single ``pytest e2e/test_concurrent_http_e2e.py``
invocation before/after a large refactor of the HTTP API surface,
the formatter, or any agent layering. Returns aggregated per-agent
PASS/FAIL even when one branch errors so one bad agent does not
mask the rest.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
)
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .helpers.api_server import (
    ApiServer,
    auth_header,
    boot_phytomni_api,
    make_async_client,
)
from .helpers.assertions import (
    assert_brief_gene_answer,
    assert_chat_answer,
    assert_data_answer,
    assert_knowledge_answer,
    assert_review_answer,
    markdown_body,
)

pytestmark = pytest.mark.live

# (tool_name, model_id_or_none, payload filename, validator). When
# ``model_id`` is set the case goes through ``/v1/chat/completions``;
# when it is None the case goes through ``/v1/agents/<slug>/runs``
# because the tool is not in ``MODEL_TO_TOOL``.
_HTTP_CASES: tuple[tuple[str, str | None, str, Callable[[str], None]], ...] = (
    ("ChatAgent", "phyto-chat", "chat_agent.json", assert_chat_answer),
    (
        "KnowledgeAgent",
        "phyto-knowledge",
        "knowledge_agent.json",
        assert_knowledge_answer,
    ),
    ("DataAgent", None, "data_agent.json", assert_data_answer),
    ("ReviewAgent", "phyto-review", "review_agent.json", assert_review_answer),
    (
        "BriefGeneAgent",
        "phyto-brief-gene",
        "brief_gene_agent.json",
        assert_brief_gene_answer,
    ),
)


@pytest.fixture(scope="session", name="api_server")
def api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the HTTP API subprocess once per session.

    Args:
        tmp_path_factory: Session tmp-dir factory for the throwaway
            SQLite stores.

    Yields:
        Live API connection details.
    """
    with boot_phytomni_api(tmp_path_factory) as server:
        yield server


@pytest_asyncio.fixture(name="api_client")
async def api_client_fixture(
    api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an async HTTP client bound to the running API base URL.

    Args:
        api_server: Running API details.

    Yields:
        ``httpx.AsyncClient`` configured with a long read timeout.
    """
    async with make_async_client(api_server) as client:
        yield client


def _user_query(demo_data_dir: Path, payload_name: str) -> str:
    """Return the ``user_query`` field from a committed demo payload.

    These five payloads carry no OBS placeholders, so reading the
    file directly avoids pulling the OBS publishing chain into the
    HTTP path.

    Args:
        demo_data_dir: Resolved ``demo_data/`` directory.
        payload_name: Payload filename, e.g. ``"chat_agent.json"``.

    Returns:
        The natural-language prompt or identifier string.
    """
    raw = (demo_data_dir / "payloads" / payload_name).read_text(
        encoding="utf-8"
    )
    return str(json.loads(raw)["user_query"])


async def _post_chat_completion(
    client: httpx.AsyncClient,
    server: ApiServer,
    model: str,
    user_query: str,
) -> str:
    """POST one chat completion and return the assistant content.

    Args:
        client: Bound async HTTP client.
        server: Running API details (for auth).
        model: OpenAI-style model id (``phyto-*``).
        user_query: Single user-message content.

    Returns:
        The plain markdown body of the assistant message
        (envelope-unwrapped through ``markdown_body`` for defense in
        depth against a pre-unwrap server).

    Raises:
        AssertionError: When the HTTP call returns non-200.
    """
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": user_query}],
        },
        headers=auth_header(server),
    )
    assert response.status_code == 200, (
        f"chat-completions {model} returned {response.status_code}: "
        f"{response.text}"
    )
    body = response.json()
    return markdown_body(body["choices"][0]["message"]["content"])


async def _post_agent_run(
    client: httpx.AsyncClient,
    server: ApiServer,
    slug: str,
    arguments: Mapping[str, Any],
) -> str:
    """POST one ``/v1/agents/{slug}/runs`` call and return ``result.answer``.

    Used for tools that are not OpenAI-compatible models, currently
    DataAgent (``slug="data"``). Accepts both the sync 200 and the
    remote-submit 202 shapes the route can return; this smoke targets
    sync agents, so we surface non-200/202 as an assertion.

    Args:
        client: Bound async HTTP client.
        server: Running API details (for auth).
        slug: Agent slug from ``_AGENT_SLUG_TO_TOOL``.
        arguments: Tool-specific kwargs forwarded as ``{"arguments": ...}``.

    Returns:
        The ``result.answer`` string from the agent.run envelope.

    Raises:
        AssertionError: When the HTTP call returns an unexpected
            status code or a malformed envelope.
    """
    response = await client.post(
        f"/v1/agents/{slug}/runs",
        json={"arguments": dict(arguments)},
        headers=auth_header(server),
    )
    assert response.status_code in (200, 202), (
        f"/v1/agents/{slug}/runs returned {response.status_code}: "
        f"{response.text}"
    )
    body = response.json()
    result = body.get("result") or {}
    return str(result.get("answer", ""))


def _collect_failure(
    case: tuple[str, str | None, str, Callable[[str], None]],
    result: str | BaseException,
) -> tuple[str, BaseException] | None:
    """Return a ``(tool_name, exception)`` pair when ``case`` failed.

    Lifting the per-case decision into a helper keeps the try/except
    outside the result-collection for-loop, so ruff's PERF203 (try/
    except in a loop) no longer applies and the loop becomes a clean
    list comprehension.

    Args:
        case: One ``_HTTP_CASES`` entry — tool name, model id, payload
            filename, validator.
        result: The corresponding element from ``asyncio.gather``'s
            return list (either the assistant content string or the
            captured exception when the call raised).

    Returns:
        ``None`` when both the HTTP call and the validator passed;
        otherwise ``(tool_name, exception)`` recording the failure
        for the aggregated assertion at the call site.
    """
    tool_name = case[0]
    if isinstance(result, BaseException):
        return (tool_name, result)
    validator = case[3]
    try:
        validator(result)
    except AssertionError as exc:
        return (tool_name, exc)
    return None


async def test_concurrent_http_e2e_five_chat_like_agents(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """Five chat-like agents pass concurrently over the live HTTP API.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details (auth).
        demo_data_dir: Resolved ``demo_data/`` directory for the four
            chat-completion payloads (which carry no OBS placeholders).
        load_payload: Loader that returns the rewritten DataAgent
            payload (kept for symmetry with the client-side smoke,
            though the four chat models do not need OBS rewriting).
    """
    coros: list[Awaitable[str]] = []
    for tool_name, model_id, payload_name, _validator in _HTTP_CASES:
        if model_id is None:
            payload = load_payload(payload_name)
            coros.append(
                _post_agent_run(
                    api_client,
                    api_server,
                    slug=tool_name.lower().replace("agent", ""),
                    arguments=payload,
                )
            )
        else:
            query = _user_query(demo_data_dir, payload_name)
            coros.append(
                _post_chat_completion(
                    api_client, api_server, model=model_id, user_query=query
                )
            )

    results = await asyncio.gather(*coros, return_exceptions=True)

    failures = [
        pair
        for pair in (
            _collect_failure(case, result)
            for case, result in zip(_HTTP_CASES, results)
        )
        if pair is not None
    ]

    assert not failures, "\n".join(
        f"{tool_name}: {detail!r}" for tool_name, detail in failures
    )
