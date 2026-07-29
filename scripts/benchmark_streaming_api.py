# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Benchmark input validation and utilities for streaming API requests."""

from __future__ import annotations

import argparse
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_WORD_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+")


class BenchmarkInputError(ValueError):
    """Raised when benchmark configuration or query input is invalid."""


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
