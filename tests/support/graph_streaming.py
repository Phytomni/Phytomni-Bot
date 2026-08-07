# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fakes for offline cited-agent graph streaming tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

DEFAULT_CITED_STREAM_REFERENCE = {
    "file_id": "f1",
    "title": "T1",
    "formatted_citation": "T1.",
    "doi_missing": True,
}


def assert_default_cited_customs(customs: Mapping[str, Any]) -> None:
    """Assert the shared terminal reference and follow-up projection."""
    assert "phyto.references" in customs
    assert customs["phyto.references"]["doc_list"] == [
        DEFAULT_CITED_STREAM_REFERENCE
    ]
    assert customs["phyto.follow_up"] == ["next?"]


def guard_network_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make unexpected async socket and DNS calls fail immediately."""
    loop_cls = asyncio.base_events.BaseEventLoop
    guarded = (
        ("create_connection", "a raw async socket"),
        ("getaddrinfo", "DNS resolution"),
    )

    def _make_raiser(label: str) -> Any:
        """Return a callable raising a named offline-escape error."""

        def _raise(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError(
                f"offline stream test escaped to {label}; a mock is missing"
            )

        return _raise

    for method_name, label in guarded:
        monkeypatch.setattr(loop_cls, method_name, _make_raiser(label))


class FakeCitedStreamApp:
    """Fake cited graph that records stream config and yields two chunks."""

    def __init__(self, *, stage_node: str, answer: str) -> None:
        """Initialize the captured config and terminal fixture values."""
        self.captured_config: Mapping[str, Any] | None = None
        self._stage_node = stage_node
        self._answer = answer

    def thread_id(self) -> str | None:
        """Return the thread id passed to ``astream``."""
        if self.captured_config is None:
            return None
        configurable = self.captured_config.get("configurable") or {}
        return configurable.get("thread_id")

    async def astream(
        self,
        _state: Mapping[str, Any],
        stream_mode: list[str],
        config: Mapping[str, Any] | None = None,
        *,
        subgraphs: bool = False,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        """Record config, then yield one stage and one terminal chunk."""
        assert stream_mode == ["custom", "updates", "values"]
        assert subgraphs is True
        self.captured_config = config
        yield ((), "updates", {self._stage_node: {}})
        yield (
            (),
            "values",
            {
                "final_response": {
                    "choices": [
                        {
                            "message": {
                                "content": self._answer,
                                "doc_list": [{"file_id": "f1", "title": "T1"}],
                                "follow_up_questions": ["next?"],
                            }
                        }
                    ]
                }
            },
        )
