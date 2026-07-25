# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared Chat invocation for BriefGene section writers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.settings import get_sensitive_config
from ...runtime.locale import SupportedLocale
from ..chat.service import phyto_chat
from ..shared.options import build_chat_kwargs


@dataclass(frozen=True, slots=True)
class BriefGenePromptRequest:
    """Immutable inputs for one rendered BriefGene prompt."""

    prompt_file: str
    prompt_path: str
    prompt_context: Mapping[str, Any]
    chat: Callable[..., Awaitable[Any]]
    config: Any
    locale: SupportedLocale | None


async def invoke_brief_gene_chat(
    user_query: str,
    chat: Callable[..., Awaitable[Any]] = phyto_chat,
    config: Any = None,
    sensitive_config: Any = None,
    locale: SupportedLocale | None = None,
) -> Any:
    """Invoke the configured Chat backend with a BriefGene locale."""
    return await chat(
        user_query=user_query,
        **build_chat_kwargs(
            {},
            config,
            sensitive_config,
            locale=locale,
        ),
    )


async def complete_brief_gene_prompt(
    request: BriefGenePromptRequest,
) -> str:
    """Render one BriefGene prompt, invoke Chat, and extract its text."""
    response = await invoke_brief_gene_chat(
        get_prompt(
            request.prompt_file,
            request.prompt_path,
            dict(request.prompt_context),
        ),
        request.chat,
        request.config,
        get_sensitive_config(),
        request.locale,
    )
    return message_content(response)
