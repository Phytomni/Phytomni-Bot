# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared MCP handler seam patches for offline server tests."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest

from mcp_server_phytomni.agents.knowledge import agent as knowledge_agent
from mcp_server_phytomni.mcp import handlers as mcp_handlers

__all__ = [
    "RunningResponseKwargs",
    "patch_chat_runtime",
    "patch_chat_completion_service",
    "patch_context_chat_runtime",
    "patch_handler_runtime",
    "patch_knowledge_agent",
    "network_run_arguments",
    "review_success_result",
]


class RunningResponseKwargs(TypedDict):
    """Typed keyword set for a persisted running agent response."""

    run_id: str
    agent: str
    status: str
    task_ids: list[str]
    result: dict[str, Any]
    persisted: bool
    degraded_tracking: bool


def network_run_arguments() -> dict[str, Any]:
    """Return the canonical no-resolution GeneNetwork request."""
    return {
        "species_code": "osa",
        "to_id": "TO:0000207",
        "resolve_to_id": False,
    }


def review_success_result() -> dict[str, Any]:
    """Return the minimal successful Review execution result."""
    return {
        "formatted": {"answer": "review ok", "metadata": {}},
        "execution": {"warnings": []},
        "raw": None,
    }


def patch_chat_runtime(
    monkeypatch: pytest.MonkeyPatch,
    chat_call_kwargs: Callable[..., dict[str, Any]],
) -> None:
    """Patch the shared Chat handler runtime and request shaping seams."""
    monkeypatch.setattr(
        mcp_handlers,
        "load_chat_runtime",
        lambda: (object(), object()),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "scratch_server_dir",
        lambda *_args: "/tmp/chat",
    )
    monkeypatch.setattr(mcp_handlers, "chat_call_kwargs", chat_call_kwargs)


def patch_chat_completion_service(
    monkeypatch: pytest.MonkeyPatch,
    fake_chat: Callable[..., Any],
) -> None:
    """Patch the Chat provider and follow-up prompt seams."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.phyto_chat",
        fake_chat,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.get_prompt",
        lambda *_args, **_kwargs: "follow-up",
    )


def patch_context_chat_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_obs_file_list: bool = False,
) -> None:
    """Patch Chat request shaping for context-enabled route tests."""

    def chat_call_kwargs(**kwargs: Any) -> dict[str, Any]:
        request = kwargs["request"]
        values = {
            "user_query": request.user_query,
            "locale": request.locale,
        }
        if include_obs_file_list:
            values["obs_file_list"] = request.obs_file_list
        return values

    patch_chat_runtime(monkeypatch, chat_call_kwargs)


def patch_handler_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scratch_path: str,
) -> None:
    """Patch the generic handler runtime used by Knowledge/Review tests."""
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=object(), obs_credentials=("a", "b")
        ),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "scratch_server_dir",
        lambda *_args: scratch_path,
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(mcp_handlers, "obs_kwargs", lambda *_args: {})


def patch_knowledge_agent(
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: Any,
    config_factory: Callable[..., Any],
    sensitive_factory: Callable[..., Any],
) -> None:
    """Patch the cached Knowledge agent and its override factories."""
    patches: dict[str, Any] = {
        "get_cached_agent": lambda *_args, **_kwargs: fake_agent,
        "_knowledge_config_with_overrides": config_factory,
        "_knowledge_sensitive_config_with_overrides": sensitive_factory,
    }
    for name, value in patches.items():
        monkeypatch.setattr(knowledge_agent, name, value)
