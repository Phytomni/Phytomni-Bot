# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for streaming API benchmark inputs and utilities."""

from __future__ import annotations

from pathlib import Path

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


def test_parse_args_rejects_nonpositive_timeout() -> None:
    """Require a positive stream inactivity timeout."""
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
                "0",
            ]
        )

    assert raised.value.code == 2
