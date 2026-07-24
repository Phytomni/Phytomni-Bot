# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared offline fakes for the Expert router's OpenAI client."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def patch_expert_router(
    monkeypatch: pytest.MonkeyPatch,
    router: ModuleType,
    completion: object,
    captured: dict[str, Any] | None = None,
) -> None:
    """Patch one Expert router module with a canned completion."""

    async def create(**kwargs: Any) -> object:
        if captured is not None:
            captured.update(kwargs)
        return completion

    def fake_async_openai(
        api_key: str, base_url: str | None
    ) -> SimpleNamespace:
        _ = (api_key, base_url)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    monkeypatch.setattr(router, "AsyncOpenAI", fake_async_openai)
    monkeypatch.setattr(
        router,
        "get_sensitive_config",
        lambda: SimpleNamespace(
            API_KEY=SimpleNamespace(get_secret_value=lambda: "k"),
            BASE_URL="https://example.invalid/v1",
            MODEL_ID="route-model",
        ),
    )
