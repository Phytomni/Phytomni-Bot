# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared offline fakes for the Expert router's OpenAI client."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.support.outbound_fakes import patch_openai_runtime


def patch_expert_router(
    monkeypatch: pytest.MonkeyPatch,
    router: ModuleType,
    completion: object,
    captured: dict[str, Any] | None = None,
    **options: Any,
) -> None:
    """Patch one Expert router module with a canned completion.

    Args:
        completion: Default object returned by ``create`` once any scripted
            ``side_effects`` are exhausted.
        captured: If given, updated with each call's kwargs (last call wins).
        side_effects: Optional per-call script. Each ``create`` call pops the
            next item; a ``BaseException`` instance is raised, anything else
            is returned. Lets a test drive the 400-then-retry fallback.
        calls: If given, every call's kwargs is appended (all calls kept),
            so a test can assert on both the initial and the retry request.
    """
    side_effects = options.pop("side_effects", None)
    calls = options.pop("calls", None)
    if options:
        unexpected = ", ".join(sorted(options))
        raise TypeError(f"unexpected router fake options: {unexpected}")
    scripted = list(side_effects or [])

    async def create(**kwargs: Any) -> object:
        if captured is not None:
            captured.update(kwargs)
        if calls is not None:
            calls.append(dict(kwargs))
        if scripted:
            result = scripted.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return completion

    def fake_async_openai(
        api_key: str, base_url: str | None
    ) -> SimpleNamespace:
        _ = (api_key, base_url)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    patch_openai_runtime(
        monkeypatch,
        router,
        fake_async_openai("k", "https://example.invalid/v1"),
    )
    monkeypatch.setattr(
        router,
        "get_sensitive_config",
        lambda: SimpleNamespace(
            API_KEY=SimpleNamespace(get_secret_value=lambda: "k"),
            BASE_URL="https://example.invalid/v1",
            MODEL_ID="route-model",
        ),
    )
