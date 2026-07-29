# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Benchmark input validation and utilities for streaming API requests."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

_WORD_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+")


class BenchmarkInputError(ValueError):
    """Raised when benchmark configuration or query input is invalid."""


class SseProtocolError(ValueError):
    """Raised when a streaming data event violates the expected shape."""


@dataclass(frozen=True)
class StreamDelta:
    """Generated text extracted from one OpenAI-compatible SSE event."""

    reasoning_content: str = ""
    content: str = ""
    done: bool = False


@dataclass(frozen=True)
class BenchmarkConfig:
    """Validated benchmark runtime configuration."""

    max_concurrency: int
    query_file: Path
    base_url: str
    model_id: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 600.0

    @property
    def endpoint(self) -> str:
        """Return the OpenAI-compatible Chat Completions endpoint."""
        return f"{self.base_url}/chat/completions"


@dataclass(frozen=True)
class QueryInput:
    """One query and its physical source-file line number."""

    line_number: int
    text: str


@dataclass(frozen=True)
class QueryResult:
    """Terminal measurement or failure for one query."""

    line_number: int
    duration: float
    ttft: float | None
    reasoning_text: str
    content_text: str
    word_count: int
    error: str | None

    @property
    def success(self) -> bool:
        """Return whether the request produced a valid streamed reply."""
        return self.error is None


@dataclass(frozen=True)
class BenchmarkSummary:
    """Aggregate measurements and request counts."""

    average_ttft: float | None
    total_duration: float
    average_query_duration: float | None
    words_per_second: float | None
    success_count: int
    failure_count: int


def count_words(text: str) -> int:
    """Count deterministic provider-independent generated word units."""
    return len(_WORD_PATTERN.findall(text))


async def iter_sse_data(
    lines: AsyncIterator[str],
) -> AsyncIterator[str]:
    """Yield complete SSE data values from decoded response lines."""
    data_lines: list[str] = []
    async for line in lines:
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        field_name, separator, value = line.partition(":")
        if field_name != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


def _string_value(value: object) -> str:
    """Return a streamed text field only when it is a string."""
    return value if isinstance(value, str) else ""


def decode_stream_delta(data: str) -> StreamDelta | None:
    """Decode one complete SSE data value into a generated-text delta."""
    if data.strip() == "[DONE]":
        return StreamDelta(done=True)
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise SseProtocolError("malformed SSE JSON") from exc
    if not isinstance(payload, dict):
        raise SseProtocolError("SSE JSON must be an object")
    choices = payload.get("choices")
    if choices is None or choices == []:
        return None
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise SseProtocolError("SSE choices must contain an object")
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        raise SseProtocolError("SSE choice delta must be an object")
    return StreamDelta(
        reasoning_content=_string_value(delta.get("reasoning_content")),
        content=_string_value(delta.get("content")),
    )


def sanitize_error(error: BaseException, api_key: str) -> str:
    """Return a bounded one-line error with the credential redacted."""
    message = " ".join(str(error).split())
    if not message:
        message = type(error).__name__
    if api_key:
        message = message.replace(api_key, "<redacted>")
    return message[:200]


async def run_query(
    config: BenchmarkConfig,
    query: QueryInput,
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> QueryResult:
    """Run and measure one streaming request inside a concurrency slot."""
    async with semaphore:
        started_at = clock()
        first_text_at: float | None = None
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        try:
            async with client.stream(
                "POST",
                config.endpoint,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.model_id,
                    "messages": [{"role": "user", "content": query.text}],
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for data in iter_sse_data(response.aiter_lines()):
                    delta = decode_stream_delta(data)
                    if delta is None:
                        continue
                    if delta.done:
                        break
                    reasoning_parts.append(delta.reasoning_content)
                    content_parts.append(delta.content)
                    has_text = (
                        delta.reasoning_content.strip()
                        or delta.content.strip()
                    )
                    if first_text_at is None and has_text:
                        first_text_at = clock()

            if first_text_at is None:
                raise SseProtocolError("stream contained no generated text")
            finished_at = clock()
            reasoning_text = "".join(reasoning_parts)
            content_text = "".join(content_parts)
            return QueryResult(
                line_number=query.line_number,
                duration=finished_at - started_at,
                ttft=first_text_at - started_at,
                reasoning_text=reasoning_text,
                content_text=content_text,
                word_count=(
                    count_words(reasoning_text) + count_words(content_text)
                ),
                error=None,
            )
        except (httpx.HTTPError, SseProtocolError) as exc:
            finished_at = clock()
            return QueryResult(
                line_number=query.line_number,
                duration=finished_at - started_at,
                ttft=None,
                reasoning_text="",
                content_text="",
                word_count=0,
                error=sanitize_error(exc, config.api_key),
            )


def summarize_results(
    results: Sequence[QueryResult],
    total_duration: float,
) -> BenchmarkSummary:
    """Aggregate successful measurements over the approved denominators."""
    successful = [result for result in results if result.success]
    success_count = len(successful)
    failure_count = len(results) - success_count
    ttft_values = [
        result.ttft for result in successful if result.ttft is not None
    ]
    average_ttft = (
        sum(ttft_values) / success_count
        if success_count and len(ttft_values) == success_count
        else None
    )
    average_query_duration = (
        sum(result.duration for result in successful) / success_count
        if success_count
        else None
    )
    words_per_second = (
        sum(result.word_count for result in successful) / total_duration
        if success_count and total_duration > 0
        else None
    )
    return BenchmarkSummary(
        average_ttft=average_ttft,
        total_duration=total_duration,
        average_query_duration=average_query_duration,
        words_per_second=words_per_second,
        success_count=success_count,
        failure_count=failure_count,
    )


async def run_benchmark(
    config: BenchmarkConfig,
    queries: Sequence[QueryInput],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[BenchmarkSummary, list[QueryResult]]:
    """Run every query through one bounded shared HTTP client."""
    if not queries:
        raise BenchmarkInputError("benchmark requires at least one query")
    semaphore = asyncio.Semaphore(config.max_concurrency)
    limits = httpx.Limits(
        max_connections=config.max_concurrency,
        max_keepalive_connections=config.max_concurrency,
    )
    timeout = httpx.Timeout(config.timeout_seconds, connect=30.0)
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        transport=transport,
    ) as client:
        started_at = clock()
        results = await asyncio.gather(
            *(
                run_query(
                    config,
                    query,
                    semaphore,
                    client,
                    clock=clock,
                )
                for query in queries
            )
        )
        total_duration = clock() - started_at
    return summarize_results(results, total_duration), list(results)


def _positive_int(value: str) -> int:
    """Parse a strictly positive integer for argparse."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_float(value: str) -> float:
    """Parse a strictly positive float for argparse."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse benchmark command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Benchmark an OpenAI-compatible streaming API."
    )
    parser.add_argument(
        "--max-concurrency",
        required=True,
        type=_positive_int,
    )
    parser.add_argument(
        "--query-file",
        required=True,
        type=Path,
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--api-key")
    parser.add_argument(
        "--timeout-seconds",
        default=600.0,
        type=_positive_float,
    )
    return parser.parse_args(argv)


def build_config(
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> BenchmarkConfig:
    """Validate parsed values and resolve the API-key environment fallback."""
    base_url = str(args.base_url).strip().rstrip("/")
    model_id = str(args.model_id).strip()
    api_key = str(args.api_key or environ.get("OPENAI_API_KEY", "")).strip()
    if not base_url:
        raise BenchmarkInputError("base URL must not be blank")
    if not model_id:
        raise BenchmarkInputError("model ID must not be blank")
    if not api_key:
        raise BenchmarkInputError("API key must not be blank")
    return BenchmarkConfig(
        max_concurrency=args.max_concurrency,
        query_file=args.query_file,
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
        timeout_seconds=args.timeout_seconds,
    )


def read_queries(path: Path) -> list[QueryInput]:
    """Read non-blank UTF-8 lines while preserving line numbers."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BenchmarkInputError(f"query file is not UTF-8: {path}") from exc
    except OSError as exc:
        raise BenchmarkInputError(
            f"query file is not readable: {path}"
        ) from exc

    queries = [
        QueryInput(line_number, line)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]
    if not queries:
        raise BenchmarkInputError("query file contains no queries")
    return queries
