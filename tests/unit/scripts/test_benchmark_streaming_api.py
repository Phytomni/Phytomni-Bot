# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for streaming API benchmark inputs and utilities."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from scripts import benchmark_streaming_api as benchmark

pytestmark = pytest.mark.unit


def test_count_words_is_provider_independent() -> None:
    """Count CJK code points and contiguous ASCII words deterministically."""
    text = "叶片 green-light GPT4 2026!"

    assert benchmark.count_words(text) == 6


def test_read_queries_ignores_blanks_and_keeps_line_numbers(
    tmp_path: Path,
) -> None:
    """Preserve non-blank query text and its physical file line."""
    query_file = tmp_path / "queries.txt"
    query_file.write_text(
        "\nfirst query\n   \n second query  \n",
        encoding="utf-8",
    )

    queries = benchmark.read_queries(query_file)

    assert queries == [
        benchmark.QueryInput(2, "first query"),
        benchmark.QueryInput(4, " second query  "),
    ]


def test_read_queries_rejects_non_utf8(tmp_path: Path) -> None:
    """Reject input that cannot be decoded as UTF-8."""
    query_file = tmp_path / "queries.txt"
    query_file.write_bytes(b"\xff")

    with pytest.raises(benchmark.BenchmarkInputError, match="UTF-8"):
        benchmark.read_queries(query_file)


def test_read_queries_rejects_no_queries(tmp_path: Path) -> None:
    """Reject files containing only blank lines."""
    query_file = tmp_path / "queries.txt"
    query_file.write_text("\n  \n", encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkInputError, match="no queries"):
        benchmark.read_queries(query_file)


def test_build_config_uses_env_key_and_hides_it_from_repr(
    tmp_path: Path,
) -> None:
    """Resolve the environment fallback without exposing the key."""
    query_file = tmp_path / "queries.txt"
    query_file.write_text("question\n", encoding="utf-8")
    args = benchmark.parse_args(
        [
            "--max-concurrency",
            "3",
            "--query-file",
            str(query_file),
            "--base-url",
            "https://example.invalid/v1/",
            "--model-id",
            "model-a",
        ]
    )

    config = benchmark.build_config(
        args,
        {"OPENAI_API_KEY": "env-secret"},
    )

    assert config.endpoint == "https://example.invalid/v1/chat/completions"
    assert config.max_concurrency == 3
    assert config.timeout_seconds == 600.0
    assert "env-secret" not in repr(config)


def test_parse_args_rejects_zero_concurrency() -> None:
    """Require at least one concurrent request slot."""
    with pytest.raises(SystemExit) as raised:
        benchmark.parse_args(
            [
                "--max-concurrency",
                "0",
                "--query-file",
                "queries.txt",
                "--base-url",
                "https://example.invalid/v1",
                "--model-id",
                "model-a",
                "--api-key",
                "secret",
            ]
        )

    assert raised.value.code == 2


@pytest.mark.parametrize("timeout", ["0", "nan", "inf"])
def test_parse_args_rejects_invalid_timeout(timeout: str) -> None:
    """Require a finite, positive stream inactivity timeout."""
    with pytest.raises(SystemExit) as raised:
        benchmark.parse_args(
            [
                "--max-concurrency",
                "1",
                "--query-file",
                "queries.txt",
                "--base-url",
                "https://example.invalid/v1",
                "--model-id",
                "model-a",
                "--api-key",
                "secret",
                "--timeout-seconds",
                timeout,
            ]
        )

    assert raised.value.code == 2


async def _line_stream(
    *lines: str,
) -> AsyncIterator[str]:
    """Yield deterministic decoded HTTP lines."""
    for line in lines:
        yield line


async def test_iter_sse_data_joins_data_lines_by_event() -> None:
    """Respect SSE event framing instead of network chunk boundaries."""
    values = [
        value
        async for value in benchmark.iter_sse_data(
            _line_stream(
                ": keepalive",
                "event: message",
                'data: {"choices":',
                'data: [{"delta": {"content": "leaf"}}]}',
                "",
            )
        )
    ]

    assert values == ['{"choices":\n[{"delta": {"content": "leaf"}}]}']


async def test_iter_sse_data_yields_final_unterminated_event() -> None:
    """Flush a final data event when the response closes without a blank."""
    values = [
        value
        async for value in benchmark.iter_sse_data(
            _line_stream('data: {"choices":[]}')
        )
    ]

    assert values == ['{"choices":[]}']


def test_decode_stream_delta_reads_reasoning_and_content() -> None:
    """Extract both generated text fields from the first choice."""
    delta = benchmark.decode_stream_delta(
        '{"choices":[{"delta":{'
        '"reasoning_content":"分析",'
        '"content":"answer"}}]}'
    )

    assert delta == benchmark.StreamDelta(
        reasoning_content="分析",
        content="answer",
        done=False,
    )


def test_decode_stream_delta_handles_done_and_usage() -> None:
    """Recognize the terminal sentinel and ignore usage-only payloads."""
    assert benchmark.decode_stream_delta("[DONE]") == benchmark.StreamDelta(
        done=True
    )
    assert (
        benchmark.decode_stream_delta(
            '{"choices":[],"usage":{"completion_tokens":4}}'
        )
        is None
    )


def test_decode_stream_delta_rejects_malformed_json() -> None:
    """Convert JSON failures into a bounded protocol error."""
    with pytest.raises(
        benchmark.SseProtocolError,
        match="malformed SSE JSON",
    ):
        benchmark.decode_stream_delta("{not-json}")


def test_decode_stream_delta_rejects_non_object_json() -> None:
    """Reject JSON values that cannot carry an OpenAI response shape."""
    with pytest.raises(benchmark.SseProtocolError, match="must be an object"):
        benchmark.decode_stream_delta("[]")


@pytest.mark.parametrize(
    "data",
    [
        '{"choices": {}}',
        '{"choices": [null]}',
    ],
)
def test_decode_stream_delta_rejects_invalid_choices_shape(data: str) -> None:
    """Require a non-empty choices list whose first value is an object."""
    with pytest.raises(
        benchmark.SseProtocolError,
        match="choices must contain an object",
    ):
        benchmark.decode_stream_delta(data)


@pytest.mark.parametrize(
    "data",
    [
        '{"choices": [{}]}',
        '{"choices": [{"delta": "not-an-object"}]}',
    ],
)
def test_decode_stream_delta_rejects_invalid_delta_shape(data: str) -> None:
    """Require the first choice to contain an object delta."""
    with pytest.raises(
        benchmark.SseProtocolError,
        match="choice delta must be an object",
    ):
        benchmark.decode_stream_delta(data)


def test_decode_stream_delta_ignores_non_string_text_values() -> None:
    """Treat non-string streamed text fields as absent without failing."""
    delta = benchmark.decode_stream_delta(
        '{"choices":[{"delta":{'
        '"reasoning_content":12,'
        '"content":["answer"]}}]}'
    )

    assert delta == benchmark.StreamDelta()


class _AsyncChunks(httpx.AsyncByteStream):
    """Yield predetermined raw response chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        """Close the in-memory stream."""


def _runtime_config(api_key: str = "top-secret") -> benchmark.BenchmarkConfig:
    """Build a minimal request configuration for transport tests."""
    return benchmark.BenchmarkConfig(
        max_concurrency=1,
        query_file=Path("queries.txt"),
        base_url="https://example.invalid/v1",
        model_id="model-a",
        api_key=api_key,
        timeout_seconds=10.0,
    )


async def test_run_query_measures_first_text_and_full_stream() -> None:
    """Measure TTFT once and count joined reasoning plus answer text."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            stream=_AsyncChunks(
                [
                    (
                        b'data: {"choices":[{"delta":'
                        b'{"role":"assistant","content":""}}]}\n\n'
                    ),
                    (
                        b'data: {"choices":[{"delta":'
                        b'{"reasoning_content":"\xe5\x88\x86\xe6\x9e\x90 "}}]}'
                        b"\n\n"
                    ),
                    (
                        b'data: {"choices":[{"delta":'
                        b'{"content":"green le"}}]}\n\n'
                    ),
                    (
                        b'data: {"choices":[{"delta":'
                        b'{"content":"af"}}]}\n\n'
                    ),
                    b"data: [DONE]\n\n",
                ]
            ),
        )

    clock = iter((10.0, 10.25, 11.0)).__next__
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await benchmark.run_query(
            _runtime_config(),
            benchmark.QueryInput(7, "question"),
            asyncio.Semaphore(1),
            client,
            clock=clock,
        )

    assert result.success is True
    assert result.ttft == pytest.approx(0.25)
    assert result.duration == pytest.approx(1.0)
    assert result.reasoning_text == "分析 "
    assert result.content_text == "green leaf"
    assert result.word_count == 4
    assert captured[0].headers["authorization"] == "Bearer top-secret"
    assert json.loads(captured[0].content) == {
        "model": "model-a",
        "messages": [{"role": "user", "content": "question"}],
        "stream": True,
    }


async def test_run_query_accepts_clean_eof_without_done() -> None:
    """Accept provider EOF after valid generated text."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            stream=_AsyncChunks(
                [b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n']
            ),
        )
    )
    clock = iter((1.0, 1.1, 2.0)).__next__
    async with httpx.AsyncClient(transport=transport) as client:
        result = await benchmark.run_query(
            _runtime_config(),
            benchmark.QueryInput(1, "question"),
            asyncio.Semaphore(1),
            client,
            clock=clock,
        )

    assert result.success is True
    assert result.content_text == "answer"


@pytest.mark.parametrize(
    ("chunk", "message"),
    [
        (b"data: {not-json}\n\n", "malformed SSE JSON"),
        (b"data: [DONE]\n\n", "no generated text"),
    ],
)
async def test_run_query_isolates_stream_failures(
    chunk: bytes,
    message: str,
) -> None:
    """Return a failed result for invalid or empty streams."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, stream=_AsyncChunks([chunk]))
    )
    clock = iter((1.0, 2.0)).__next__
    async with httpx.AsyncClient(transport=transport) as client:
        result = await benchmark.run_query(
            _runtime_config(),
            benchmark.QueryInput(4, "private question"),
            asyncio.Semaphore(1),
            client,
            clock=clock,
        )

    assert result.success is False
    assert result.error is not None
    assert message in result.error
    assert "private question" not in result.error


async def test_run_query_does_not_retry_http_failure_or_print_body() -> None:
    """Make one attempt and exclude response bodies from the failure."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, text="top-secret backend body")

    transport = httpx.MockTransport(handler)
    clock = iter((1.0, 2.0)).__next__
    async with httpx.AsyncClient(transport=transport) as client:
        result = await benchmark.run_query(
            _runtime_config(),
            benchmark.QueryInput(3, "question"),
            asyncio.Semaphore(1),
            client,
            clock=clock,
        )

    assert calls == 1
    assert result.success is False
    assert result.error is not None
    assert "top-secret" not in result.error


def test_sanitize_error_redacts_and_bounds_api_key() -> None:
    """Redact credentials before applying the error-length cap."""
    error = ValueError("top-secret " + ("x" * 300))

    sanitized = benchmark.sanitize_error(error, "top-secret")

    assert sanitized.startswith("<redacted>")
    assert "top-secret" not in sanitized
    assert len(sanitized) == 200
