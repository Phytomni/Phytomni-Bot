# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Reusable assembly helpers for ``mcp.handlers``.

Factor each MCP handler's recurring kwargs spreads (chat / retrieve /
OBS / retry / coder / analysis platform) into focused builders so the
eleven handlers keep only argument binding and one wrapper call. The
``chat`` shape delegates to ``agents.shared.options.build_chat_kwargs``
so phyto_chat defaults stay in one place. Zero business logic.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..agents.shared.options import build_chat_kwargs
from ..config.defaults import (
    AnalystConfig,
    ChatConfig,
    KnowledgeConfig,
    ServerConfig,
)
from ..config.settings import SensitiveConfig
from ..runtime.locale import SupportedLocale
from .schemas import ChatAgent

__all__ = [
    "HandlerRuntime",
    "analysis_platform_kwargs",
    "chat_call_kwargs",
    "chat_kwargs",
    "coder_kwargs",
    "load_chat_runtime",
    "load_handler_runtime",
    "obs_kwargs",
    "retrieve_kwargs",
    "retry_kwargs",
]


class HandlerRuntime(NamedTuple):
    """Sensitive config + OBS credentials loaded for one handler call.

    Attributes:
        sensitive: Decrypted sensitive configuration bundle.
        obs_credentials: ``(access_key_id, secret_access_key)`` tuple,
            unpacked once so handlers do not call
            ``sensitive.obs_credentials()`` twice per call.
    """

    sensitive: SensitiveConfig
    obs_credentials: tuple[str, str]


def load_handler_runtime() -> HandlerRuntime:
    """Load sensitive config and OBS credentials for the current handler."""
    sensitive = SensitiveConfig.load()
    return HandlerRuntime(
        sensitive=sensitive,
        obs_credentials=sensitive.obs_credentials(),
    )


def load_chat_runtime() -> tuple[ChatConfig, HandlerRuntime]:
    """Load the Chat config and sensitive handler runtime together."""
    return ChatConfig(), load_handler_runtime()


def chat_kwargs(
    config: ChatConfig,
    sensitive: SensitiveConfig,
    *,
    locale: SupportedLocale | None = None,
) -> dict[str, Any]:
    """Return phyto_chat-flavored kwargs for a handler wrapper call.

    Delegates to the shared ``build_chat_kwargs`` for the 19-field
    chat + retry block (prompt / api / model / sampling / timeout /
    retries) and overlays ``response_format`` and ``max_tokens`` from
    the handler's config defaults so wrappers that take those extras
    can spread one dict instead of two.

    Typed to ``ChatConfig`` because ``RESPONSE_FORMAT`` first appears
    on that subclass; ServerConfig alone is not enough.
    """
    kwargs = build_chat_kwargs({}, config, sensitive, locale=locale)
    kwargs["response_format"] = config.RESPONSE_FORMAT
    kwargs["max_tokens"] = config.MAX_TOKENS
    return kwargs


def chat_call_kwargs(
    request: ChatAgent,
    server_dir: str,
    config: ChatConfig,
    runtime: HandlerRuntime,
) -> dict[str, Any]:
    """Return the complete provider kwargs for a chat execution path."""
    return {
        "user_query": request.user_query,
        "obs_file_list": request.obs_file_list,
        "server_dir": server_dir,
        **chat_kwargs(config, runtime.sensitive, locale=request.locale),
        **obs_kwargs(config, runtime.obs_credentials),
    }


def retrieve_kwargs(config: KnowledgeConfig) -> dict[str, Any]:
    """Return retrieve and rerank kwargs sourced from a handler's config.

    Typed to ``KnowledgeConfig`` because the retrieve-pipeline knobs
    (PAGE_NUM / FILTER_STRING / SCOPE / EXTRA_REPO_IDS / TOP_N /
    SCORE_THRESHOLD / RERANK_BATCH_SIZE) first appear there.
    """
    return {
        "retrieve_url": config.RETRIEVE_URL,
        "repo_id_dict": config.REPO_ID_DICT,
        "page_num": config.PAGE_NUM,
        "filter_string": config.FILTER_STRING,
        "scope": config.SCOPE,
        "extra_repo_ids": config.EXTRA_REPO_IDS,
        "rerank_url": config.RERANK_URL,
        "rerank_batch_size": config.RERANK_BATCH_SIZE,
        "score_threshold": config.SCORE_THRESHOLD,
        "top_n": config.TOP_N,
    }


def obs_kwargs(
    config: ServerConfig, credentials: tuple[str, str]
) -> dict[str, Any]:
    """Return OBS storage kwargs from a handler's config and credentials."""
    access_key_id, secret_access_key = credentials
    return {
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "obs_server": config.OBS_SERVER,
        "bucket_name": config.BUCKET_NAME,
        "part_size": config.PART_SIZE,
        "task_num": config.TASK_NUM,
        "max_concurrency": config.MAX_CONCURRENCY,
        "max_workers": config.MAX_WORKERS,
    }


def retry_kwargs(config: ServerConfig) -> dict[str, Any]:
    """Return retry policy kwargs sourced from a handler's config.

    Returned separately from ``chat_kwargs`` so non-chat handlers
    (data, brief_gene direct BI paths) can spread retry settings
    without pulling the entire chat block.
    """
    return {
        "timeout": config.TIMEOUT,
        "retriable_codes": config.RETRIABLE_CODES,
        "max_retries": config.MAX_RETRIES,
    }


def coder_kwargs(sensitive: SensitiveConfig) -> dict[str, Any]:
    """Return coder-model kwargs sourced from the sensitive config."""
    return {
        "model_url": sensitive.CODER_URL,
        "model_name": sensitive.CODER_MODEL,
        "coder_api_key": sensitive.CODER_API_KEY.get_secret_value(),
    }


def analysis_platform_kwargs(config: AnalystConfig) -> dict[str, Any]:
    """Return analysis-platform kwargs sourced from a handler's config.

    Typed to ``AnalystConfig`` because the analysis-platform knobs
    (RESOURCE / APP_ID) first appear on that subclass; the simpler
    URL fields exist on the ServerConfig base.
    """
    return {
        "analysis_url": config.ANALYSIS_URL,
        "region": config.ANALYSIS_REGION,
        "resource_dict": config.RESOURCE,
        "app_id_dict": config.APP_ID,
    }
