# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the RERANK_CONCURRENCY deployment-level config knob.

Pins the default value and the dual env-alias behavior (unprefixed
``RERANK_CONCURRENCY`` and prefixed ``PHYTOMNI_RERANK_CONCURRENCY``),
the latter being the reason the field must carry an explicit
``AliasChoices`` rather than the bare ``RERANK_BATCH_SIZE`` shape.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.config.defaults import KnowledgeConfig

pytestmark = pytest.mark.unit


def test_rerank_concurrency_defaults_to_16() -> None:
    """The throttle default is 16 (a 2x margin under the N~=32 knee)."""
    assert KnowledgeConfig().RERANK_CONCURRENCY == 16


def test_rerank_concurrency_reads_unprefixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unprefixed ``RERANK_CONCURRENCY`` env overrides the default."""
    monkeypatch.setenv("RERANK_CONCURRENCY", "8")
    assert KnowledgeConfig().RERANK_CONCURRENCY == 8


def test_rerank_concurrency_reads_phytomni_prefixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``PHYTOMNI_RERANK_CONCURRENCY`` alias overrides the default.

    A bare pydantic-settings field would ignore the prefixed form, so
    this asserts the field carries an explicit ``AliasChoices``.
    """
    monkeypatch.setenv("PHYTOMNI_RERANK_CONCURRENCY", "24")
    assert KnowledgeConfig().RERANK_CONCURRENCY == 24
