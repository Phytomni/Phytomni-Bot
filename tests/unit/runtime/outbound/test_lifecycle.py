# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for atomic publication and cleanup of the outbound runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.support.outbound_fakes import (
    QueueTransport,
    RecordingResources,
    recording_outbound_runtime,
)

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundRuntimeStateError,
    aclose_outbound_runtime,
    current_outbound_runtime,
    init_outbound_runtime,
)

pytestmark = pytest.mark.unit


def _config(**overrides: Any) -> ServerConfig:
    """Build a test server configuration."""
    return ServerConfig(**overrides)


@pytest.fixture(autouse=True)
async def _clear_runtime() -> Any:
    """Prevent process runtime state leaking between lifecycle tests."""
    await aclose_outbound_runtime()
    yield
    await aclose_outbound_runtime()


@pytest.mark.asyncio
async def test_partial_startup_rolls_back_without_publication() -> None:
    """A second-profile constructor failure closes only the first profile."""
    resources = RecordingResources(fail_on="direct_upstream")

    with pytest.raises(RuntimeError, match="direct_upstream"):
        await init_outbound_runtime(_config(), factories=resources.factories())

    with pytest.raises(OutboundRuntimeStateError):
        current_outbound_runtime()
    assert resources.closed == ["trusted"]


@pytest.mark.asyncio
async def test_repeated_init_rejected_and_close_is_exactly_once() -> None:
    """The process slot has one owner and cleanup is idempotent."""
    resources = RecordingResources()
    runtime = await init_outbound_runtime(
        _config(), factories=resources.factories()
    )

    with pytest.raises(OutboundRuntimeStateError, match="initialized"):
        await init_outbound_runtime(
            _config(), factories=RecordingResources().factories()
        )
    assert current_outbound_runtime() is runtime

    await aclose_outbound_runtime()
    await aclose_outbound_runtime()

    assert resources.closed == ["obs", "direct_upstream", "trusted"]
    with pytest.raises(OutboundRuntimeStateError):
        current_outbound_runtime()


@pytest.mark.asyncio
async def test_close_rejects_waiter_and_waits_for_borrower() -> None:
    """Shutdown drains the borrower and wakes queued work before close."""
    transport = QueueTransport()
    resources = RecordingResources(transport=transport)
    runtime = await init_outbound_runtime(
        _config(OUTBOUND_RETRIEVAL_CONCURRENCY=1),
        factories=resources.factories(),
    )
    borrower_entered = asyncio.Event()
    waiter_started = asyncio.Event()
    release_borrower = asyncio.Event()

    async def borrow() -> None:
        async with runtime.pools.lease(OutboundPoolName.RETRIEVAL):
            borrower_entered.set()
            await release_borrower.wait()

    async def wait() -> None:
        waiter_started.set()
        async with runtime.pools.lease(OutboundPoolName.RETRIEVAL):
            pytest.fail("waiter entered during shutdown")

    borrower = asyncio.create_task(borrow())
    await borrower_entered.wait()
    waiter = asyncio.create_task(wait())
    await waiter_started.wait()
    for _ in range(100):
        if runtime.pools.snapshot(OutboundPoolName.RETRIEVAL).waiting == 1:
            break
        await asyncio.sleep(0)
    else:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        release_borrower.set()
        await borrower
        await aclose_outbound_runtime()
        pytest.fail("waiter did not reach the outbound pool queue")
    closing = asyncio.create_task(aclose_outbound_runtime())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="closing"):
        current_outbound_runtime()
    with pytest.raises(RuntimeError, match="closing"):
        await waiter
    assert not closing.done()
    assert not resources.closed
    release_borrower.set()
    await borrower
    await closing
    assert resources.closed == ["obs", "direct_upstream", "trusted"]


class _RecordingStream:
    """Provider stream whose iteration lifetime is externally controlled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = 0

    def __aiter__(self) -> Any:
        """Return the asynchronous iterator for one delayed chunk."""
        return self._iterate()

    async def _iterate(self) -> Any:
        self.started.set()
        await self.release.wait()
        yield SimpleNamespace(
            model_dump=lambda: {
                "id": "stream-1",
                "choices": [{"delta": {"content": "ok"}}],
            }
        )

    async def close(self) -> None:
        """Record downstream generator cleanup."""
        self.closed += 1


class _RecordingOpenAI:
    """Small OpenAI fake for the shared-client and shared-pool proof."""

    def __init__(self) -> None:
        self.stream: _RecordingStream | None = None
        self.stream_created = asyncio.Event()
        self.completion_calls = 0
        self.close_calls = 0
        self.http_client: Any = None
        self.base_url = "https://provider.invalid/v1"
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    async def create(self, **kwargs: Any) -> Any:
        """Return a delayed stream or one valid buffered completion."""
        if kwargs["stream"]:
            self.stream = _RecordingStream()
            self.stream_created.set()
            return self.stream
        self.completion_calls += 1
        return SimpleNamespace(
            model_dump=lambda: {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "completion",
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    async def close(self) -> None:
        """Close the dedicated HTTPX client exactly once."""
        self.close_calls += 1
        await self.http_client.aclose()


@pytest.mark.asyncio
async def test_startup_failure_closes_openai_owner_before_rollback() -> None:
    """A later OBS failure cannot leak the already-built OpenAI owner."""
    fake = _RecordingOpenAI()

    def openai_factory(**kwargs: Any) -> Any:
        fake.http_client = kwargs["http_client"]
        return fake

    def failing_obs(**_kwargs: Any) -> Any:
        raise RuntimeError("obs-startup")

    resources = RecordingResources(
        openai_factory=openai_factory,
        obs_client_factory=failing_obs,
    )

    with pytest.raises(RuntimeError, match="obs-startup"):
        await init_outbound_runtime(_config(), factories=resources.factories())

    assert fake.close_calls == 1
    assert fake.http_client is not None
    assert fake.http_client.is_closed
    assert resources.closed == ["direct_upstream", "trusted"]


@pytest.mark.asyncio
async def test_openai_client_shares_llm_pool_across_stream_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stream occupies the combined LLM pool until its generator closes."""
    fake = _RecordingOpenAI()

    def openai_factory(**kwargs: Any) -> Any:
        fake.http_client = kwargs["http_client"]
        fake.base_url = str(kwargs["base_url"])
        return fake

    resources = RecordingResources(openai_factory=openai_factory)
    monkeypatch.setattr(chat_service, "get_prompt", lambda *_args: "prompt")
    async with recording_outbound_runtime(
        config=_config(
            OUTBOUND_LLM_CONCURRENCY=1,
            HTTP_MAX_CONNECTIONS=17,
            HTTP_MAX_KEEPALIVE=9,
        ),
        resources=resources,
    ) as runtime:
        stream = chat_service.stream_phyto_chat_chunks(
            "query",
            [],
            prompt_file="unused",
            prompt_path="unused",
            api_key="ignored",
            base_url="ignored",
            model="pytest-model",
            response_format={"type": "text"},
            timeout=1.0,
            max_retries=0,
        )

        async def next_chunk() -> dict[str, Any]:
            return await anext(stream)

        first = asyncio.create_task(next_chunk())
        closable_stream = getattr(stream, "aclose")
        await asyncio.wait_for(fake.stream_created.wait(), timeout=1)
        assert fake.stream is not None
        await fake.stream.started.wait()

        sampling_options = {
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        }
        completion_call = {
            "messages": [
                {
                    "role": "user",
                    "content": f"completion-{id(fake)}",
                }
            ],
            "model": "pytest-model",
            "temperature": 0.0,
            **sampling_options,
            "n": 1,
            "max_tokens": None,
            "reasoning_effort": None,
            "api_key": "ignored",
            "base_url": "ignored",
            "user": "test",
            "timeout": 1.0,
            "stream": False,
            "response_format": {"type": "text"},
        }
        completion = asyncio.create_task(
            chat_service.run_phyto_chat_cached(
                **cast(chat_service.ChatCacheCall, completion_call)
            )
        )
        for _ in range(1000):
            snapshot = runtime.pools.snapshot(OutboundPoolName.LLM)
            if snapshot.waiting == 1:
                break
            await asyncio.sleep(0.001)
        else:
            fake.stream.release.set()
            await first
            await closable_stream()
            await completion
            pytest.fail("completion did not queue behind the stream")
        assert fake.completion_calls == 0
        assert snapshot.waiting == 1

        fake.stream.release.set()
        await first
        await asyncio.wait_for(closable_stream(), timeout=1)
        await completion

    assert fake.completion_calls == 1
    assert fake.close_calls == 1
    assert fake.stream is not None
    assert fake.stream.closed == 1
    assert fake.http_client is not None
    assert fake.http_client.is_closed
    assert fake.http_client is not resources.clients["trusted"]
    assert fake.http_client.is_closed
    transport = getattr(fake.http_client, "_transport")
    pool = getattr(transport, "_pool")
    assert getattr(pool, "_max_connections") == 17
    assert getattr(pool, "_max_keepalive_connections") == 9
