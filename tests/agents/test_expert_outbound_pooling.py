# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Production-seam evidence for Expert use of the shared LLM pool."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from openai import BadRequestError

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.mcp.schemas import agent_openai_tool_specs
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolSnapshot,
    aclose_outbound_runtime,
)
from tests.support.outbound_fakes import (
    RecordingResources,
    bounded_await,
    managed_async_task,
    recording_outbound_runtime,
)

pytestmark = pytest.mark.agent

_REQUIRED_REJECTION = (
    'tool_choice must either be a named tool or "auto". '
    'tool_choice="required" is not supported'
)


class _OpenAIHarness:
    """Expose one scripted provider through the runtime resource boundary."""

    def __init__(self, create: Callable[..., Awaitable[Any]]) -> None:
        self._create = create
        self.http_client: httpx.AsyncClient | None = None
        self.base_url = "https://example.invalid/v1"
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def factory(self, **kwargs: Any) -> Any:
        """Bind the runtime-owned HTTP client and return this provider."""
        self.http_client = cast(httpx.AsyncClient, kwargs["http_client"])
        return self

    async def close(self) -> None:
        """Close the dedicated SDK HTTP transport exactly once."""
        assert self.http_client is not None
        await self.http_client.aclose()


class _DeliveredChatStream:
    """Keep a delivered Chat stream open until the caller resumes it."""

    def __init__(self) -> None:
        self.closed = 0

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        """Return an iterator that exposes one provider chunk."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[SimpleNamespace]:
        """Yield one chunk while the outer Chat generator retains its lease."""
        yield SimpleNamespace(
            model_dump=lambda: {
                "id": "shared-pool-stream",
                "choices": [{"delta": {"content": "held"}}],
            }
        )

    async def close(self) -> None:
        """Record provider-stream closure by the production adapter."""
        self.closed += 1


def _config(**overrides: Any) -> ServerConfig:
    """Build a test configuration while preserving settings sources."""
    return ServerConfig(**overrides)


def _completion() -> SimpleNamespace:
    """Return one valid provider completion for the Expert adapter."""
    return SimpleNamespace(choices=[SimpleNamespace(message="selected")])


def _bad_request(message: str) -> BadRequestError:
    """Build an offline OpenAI 400 response."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body=None)


def _patch_expert_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep provider configuration offline and deterministic."""
    monkeypatch.setattr(
        expert_router,
        "get_sensitive_config",
        lambda: SimpleNamespace(MODEL_ID="route-model"),
    )


async def _wait_for_llm_waiter(runtime: Any, task: asyncio.Task[Any]) -> None:
    """Wait boundedly for one caller to queue behind the active LLM lease."""

    async def wait() -> None:
        while runtime.pools.snapshot(OutboundPoolName.LLM).waiting != 1:
            if task.done():
                await task
                raise AssertionError("Expert completed before entering queue")
            await asyncio.sleep(0)

    await bounded_await(wait())


def _assert_settled(
    snapshot: OutboundPoolSnapshot,
    *,
    started: int,
    completed: int,
    failed: int,
) -> None:
    """Assert common terminal invariants for the capacity-one LLM pool."""
    assert snapshot.in_use == 0
    assert snapshot.waiting == 0
    assert snapshot.max_in_use == 1
    assert snapshot.started == started
    assert snapshot.completed == completed
    assert snapshot.failed == failed
    assert snapshot.cancelled == 0


@pytest.fixture(autouse=True)
async def _isolate_runtime_and_endpoint_cache() -> AsyncIterator[None]:
    """Keep runtime and unsupported-endpoint state local to each test."""
    unsupported = getattr(expert_router, "_TOOL_CHOICE_REQUIRED_UNSUPPORTED")
    await aclose_outbound_runtime()
    unsupported.clear()
    yield
    unsupported.clear()
    await aclose_outbound_runtime()


async def test_expert_waits_for_delivered_chat_stream_in_shared_llm_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove active Chat and Expert calls share capacity and counters."""
    calls: list[dict[str, Any]] = []
    provider_stream = _DeliveredChatStream()

    async def create(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        if kwargs.get("stream") is True:
            return provider_stream
        return _completion()

    harness = _OpenAIHarness(create)
    monkeypatch.setattr(chat_service, "get_prompt", lambda *_args: "prompt")
    _patch_expert_settings(monkeypatch)
    async with recording_outbound_runtime(
        config=_config(OUTBOUND_LLM_CONCURRENCY=1),
        resources=RecordingResources(openai_factory=harness.factory),
    ) as runtime:
        stream = chat_service.stream_phyto_chat_chunks(
            "query",
            prompt_file="unused",
            prompt_path="unused",
            model="stream-model",
            response_format={"type": "text"},
            timeout=1.0,
            max_retries=0,
        )
        close_stream = getattr(stream, "aclose")
        try:
            first = await bounded_await(anext(stream))
            assert first["choices"][0]["delta"]["content"] == "held"
            assert runtime.pools.snapshot(OutboundPoolName.LLM).in_use == 1

            async with managed_async_task(
                expert_router.complete_expert_routing(
                    messages=[{"role": "user", "content": "route"}],
                    tools=agent_openai_tool_specs(),
                    tool_choice="auto",
                )
            ) as expert:
                await _wait_for_llm_waiter(runtime, expert)
                blocked = runtime.pools.snapshot(OutboundPoolName.LLM)
                assert len(calls) == 1
                assert blocked.in_use == 1
                assert blocked.waiting == 1
                assert blocked.max_in_use == 1
                assert blocked.started == 1

                with pytest.raises(StopAsyncIteration):
                    await bounded_await(anext(stream))
                result = await bounded_await(expert)
                assert result.choices
        finally:
            await bounded_await(close_stream())

        _assert_settled(
            runtime.pools.snapshot(OutboundPoolName.LLM),
            started=2,
            completed=2,
            failed=0,
        )

    assert provider_stream.closed == 1


async def test_expert_retry_releases_llm_pool_during_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each transient retry reacquires after sleeping with no LLM lease."""
    entry_snapshots: list[OutboundPoolSnapshot] = []
    sleep_snapshots: list[OutboundPoolSnapshot] = []

    async def create(**_kwargs: Any) -> Any:
        entry_snapshots.append(runtime.pools.snapshot(OutboundPoolName.LLM))
        if len(entry_snapshots) == 1:
            raise httpx.TimeoutException("transient timeout")
        return _completion()

    async def fake_sleep(_delay: float) -> None:
        sleep_snapshots.append(runtime.pools.snapshot(OutboundPoolName.LLM))

    harness = _OpenAIHarness(create)
    monkeypatch.setattr(
        expert_router,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep),
    )
    _patch_expert_settings(monkeypatch)
    async with recording_outbound_runtime(
        config=_config(OUTBOUND_LLM_CONCURRENCY=1),
        resources=RecordingResources(openai_factory=harness.factory),
    ) as runtime:
        result = await bounded_await(
            expert_router.complete_expert_routing(
                messages=[{"role": "user", "content": "route"}],
                tools=agent_openai_tool_specs(),
                tool_choice="auto",
            )
        )
        assert result.choices

        assert len(entry_snapshots) == 2
        assert entry_snapshots[0].in_use == 1
        assert entry_snapshots[0].started == 1
        assert entry_snapshots[1].in_use == 1
        assert entry_snapshots[1].started == 2
        assert all(snapshot.max_in_use == 1 for snapshot in entry_snapshots)
        assert len(sleep_snapshots) == 1
        assert sleep_snapshots[0].in_use == 0
        assert sleep_snapshots[0].started == 1
        assert sleep_snapshots[0].failed == 1

        _assert_settled(
            runtime.pools.snapshot(OutboundPoolName.LLM),
            started=2,
            completed=1,
            failed=1,
        )


async def test_expert_constrained_fallback_reacquires_llm_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constrained-choice 400 releases before one fresh auto attempt."""
    calls: list[dict[str, Any]] = []
    entry_snapshots: list[OutboundPoolSnapshot] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        entry_snapshots.append(runtime.pools.snapshot(OutboundPoolName.LLM))
        if len(calls) == 1:
            raise _bad_request(_REQUIRED_REJECTION)
        return _completion()

    harness = _OpenAIHarness(create)
    _patch_expert_settings(monkeypatch)
    async with recording_outbound_runtime(
        config=_config(OUTBOUND_LLM_CONCURRENCY=1),
        resources=RecordingResources(openai_factory=harness.factory),
    ) as runtime:
        result = await bounded_await(
            expert_router.complete_expert_routing(
                messages=[{"role": "user", "content": "route"}],
                tools=agent_openai_tool_specs(),
                tool_choice="required",
            )
        )
        assert result.choices
        assert [call["tool_choice"] for call in calls] == [
            "required",
            "auto",
        ]

        assert len(entry_snapshots) == 2
        assert entry_snapshots[0].in_use == 1
        assert entry_snapshots[0].started == 1
        assert entry_snapshots[0].waiting == 0
        assert entry_snapshots[1].in_use == 1
        assert entry_snapshots[1].started == 2
        assert entry_snapshots[1].failed == 1
        assert entry_snapshots[1].waiting == 0
        assert all(snapshot.max_in_use == 1 for snapshot in entry_snapshots)

        _assert_settled(
            runtime.pools.snapshot(OutboundPoolName.LLM),
            started=2,
            completed=1,
            failed=1,
        )
