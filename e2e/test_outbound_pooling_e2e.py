# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Gated non-production HTTP Chat acceptance for outbound pooling."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .helpers.outbound_pooling import (
    MissingOutboundLiveGateError,
    live_server_environment,
    require_live_gates,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.network,
]

LIVE_SCENARIO_TESTS: dict[str, str] = {
    "llm_stream_and_completion": (
        "test_chat_stream_and_completion_share_llm_pool"
    ),
    "retrieval_and_rerank": (
        "test_knowledge_and_data_agents_use_platform_pools"
    ),
    "nl2sql": "test_knowledge_and_data_agents_use_platform_pools",
}


def _chat_timeout_seconds() -> float:
    """Return the explicitly overridable large Chat API read timeout."""
    raw = os.environ.get("PHYTOMNI_OUTBOUND_POOL_E2E_CHAT_TIMEOUT_SECONDS")
    timeout = float(raw) if raw else 3600.0
    if not 0 < timeout <= 7200:
        raise ValueError(
            "PHYTOMNI_OUTBOUND_POOL_E2E_CHAT_TIMEOUT_SECONDS must be "
            "between 0 and 7200 seconds"
        )
    return timeout


@pytest.fixture(scope="session", name="outbound_api_server")
def outbound_api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Any]:
    """Start the real API only after all four safety gates pass."""
    try:
        require_live_gates(os.environ)
    except MissingOutboundLiveGateError as exc:
        pytest.skip(str(exc))

    # Keep package/configuration imports behind the explicit live gate.
    api_server_helpers = importlib.import_module(
        ".helpers.api_server", package=__package__
    )
    citation_helpers = importlib.import_module(
        ".helpers.citation_database", package=__package__
    )

    citation_root = tmp_path_factory.mktemp("outbound-citation")
    with citation_helpers.configured_e2e_citation_database(
        citation_root, force_disposable=True
    ) as database:
        assert database is not None
        with api_server_helpers.boot_phytomni_api(
            tmp_path_factory,
            environment={
                **live_server_environment(),
                "CITATION_DB_PATH": str(database),
            },
        ) as server:
            yield server


@pytest_asyncio.fixture(name="outbound_api_client")
async def outbound_api_client_fixture(
    outbound_api_server: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a long-read client for the gated Chat API probe."""
    timeout = httpx.Timeout(
        connect=5.0,
        read=_chat_timeout_seconds(),
        write=10.0,
        pool=5.0,
    )
    async with httpx.AsyncClient(
        base_url=outbound_api_server.base_url,
        timeout=timeout,
    ) as client:
        yield client


def _auth_header(server: Any) -> dict[str, str]:
    """Resolve the live auth helper only after the server gate ran."""
    module = importlib.import_module(
        ".helpers.api_server", package=__package__
    )

    return module.auth_header(server)


async def _chat(
    client: httpx.AsyncClient,
    server: Any,
    query: str,
    *,
    model: str = "phyto-chat",
) -> httpx.Response:
    """Send one synthetic Chat API request with a bounded large timeout."""
    return await client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": query}],
        },
        headers=_auth_header(server),
    )


async def _chat_stream(
    client: httpx.AsyncClient,
    server: Any,
    query: str,
) -> int:
    """Consume one OpenAI-compatible Chat stream to EOF."""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": query}],
            "stream": True,
        },
        headers=_auth_header(server),
    ) as response:
        assert response.status_code == 200, (await response.aread())[:500]
        frame_count = 0
        saw_done = False
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if payload == "[DONE]":
                saw_done = True
                break
            event = json.loads(payload)
            assert isinstance(event, dict)
            frame_count += 1
        assert saw_done
        return frame_count


async def _agent_run_answer(
    client: httpx.AsyncClient,
    server: Any,
    slug: str,
    arguments: dict[str, Any],
) -> str:
    """Run one native HTTP agent and return its terminal answer."""
    response = await client.post(
        f"/v1/agents/{slug}/runs",
        json={"arguments": arguments},
        headers=_auth_header(server),
    )
    assert response.status_code in (200, 202), response.text[:500]
    body = response.json()
    if response.status_code == 202:
        module = importlib.import_module(
            ".helpers.polling", package=__package__
        )

        run_id = body.get("id")
        assert isinstance(run_id, str) and run_id
        terminal = await module.poll_http_run_to_terminal(
            client,
            run_id,
            headers=_auth_header(server),
            timeout_seconds=_chat_timeout_seconds(),
            poll_interval_seconds=5.0,
        )
        result = terminal.result
    else:
        result = body.get("result") or {}
    formatted = result.get("formatted") or {}
    answer = formatted.get("answer", "")
    assert isinstance(answer, str) and answer.strip()
    return answer


async def test_chat_api_reuses_llm_pool_under_parallel_requests(
    outbound_api_client: httpx.AsyncClient,
    outbound_api_server: Any,
) -> None:
    """Two disposable Chat calls share the process-owned LLM pool."""
    responses = await asyncio.gather(
        _chat(
            outbound_api_client,
            outbound_api_server,
            "Answer with one short sentence about a synthetic plant trait.",
        ),
        _chat(
            outbound_api_client,
            outbound_api_server,
            "Answer with one short sentence about a synthetic gene.",
        ),
    )
    for response in responses:
        assert response.status_code == 200, response.text[:500]
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        assert isinstance(content, str) and content.strip()


async def test_chat_stream_and_completion_share_llm_pool(
    outbound_api_client: httpx.AsyncClient,
    outbound_api_server: Any,
) -> None:
    """A stream and completion share the deliberately small LLM pool."""
    streamed_frames, completion = await asyncio.gather(
        _chat_stream(
            outbound_api_client,
            outbound_api_server,
            "Answer with one short sentence about a synthetic plant trait.",
        ),
        _chat(
            outbound_api_client,
            outbound_api_server,
            "Answer with one short sentence about a synthetic gene.",
        ),
    )
    assert streamed_frames > 0
    assert completion.status_code == 200, completion.text[:500]
    content = completion.json()["choices"][0]["message"]["content"]
    assert isinstance(content, str) and content.strip()


async def test_knowledge_and_data_agents_use_platform_pools(
    outbound_api_client: httpx.AsyncClient,
    outbound_api_server: Any,
) -> None:
    """Exercise retrieve/rerank and NL2SQL on the same API runtime."""
    knowledge_query = (
        "Which gene families and signalling pathways are most strongly "
        "implicated in drought tolerance in wheat? Mention two loci."
    )
    data_payload = {
        "user_query": (
            "What are the homologous genes of Os01g0177400 in wheat? "
            "List up to ten orthologs with gene IDs and identity scores."
        )
    }

    knowledge_response, data_answer = await asyncio.gather(
        _chat(
            outbound_api_client,
            outbound_api_server,
            knowledge_query,
            model="phyto-knowledge",
        ),
        _agent_run_answer(
            outbound_api_client,
            outbound_api_server,
            "data",
            data_payload,
        ),
    )

    assert knowledge_response.status_code == 200, knowledge_response.text[:500]
    knowledge_body = knowledge_response.json()
    knowledge_content = knowledge_body["choices"][0]["message"]["content"]
    assert isinstance(knowledge_content, str) and knowledge_content.strip()
    assert data_answer.strip()
