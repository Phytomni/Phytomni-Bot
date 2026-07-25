# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review graph acquisition for HTTP and MCP streaming callers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import get_sensitive_config
from ...runtime.locale import SupportedLocale
from .state import DeepResearchState


@dataclass(frozen=True)
class ReviewStreamConfig:
    """Config and override maps shared by blocking and stream paths."""

    review_config: Any
    review_config_field_map: Mapping[str, str]
    review_sensitive_field_map: Mapping[str, str]
    review_secret_field_map: Mapping[str, str]


@dataclass(frozen=True)
class ReviewStreamDependencies:
    """Dependencies needed to acquire the cached review graph."""

    config: ReviewStreamConfig
    agent_factory: Callable[..., Any]
    knowledge_factory: Callable[..., Any]
    get_cached_agent: Callable[..., Any]
    agent_fingerprint_values: Callable[..., Any]


def build_review_stream_target(
    user_query: str,
    obs_file_list: list[str] | None,
    locale: SupportedLocale | None,
    dependencies: ReviewStreamDependencies,
) -> tuple[Any, DeepResearchState]:
    """Acquire the cached review app and seed its initial state."""
    review_config = copy_config_with_overrides(
        dependencies.config.review_config,
        {},
        dependencies.config.review_config_field_map,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        {},
        field_map=dependencies.config.review_sensitive_field_map,
        secret_field_map=dependencies.config.review_secret_field_map,
    )
    agent = dependencies.get_cached_agent(
        "DeepResearchAgent",
        lambda: dependencies.agent_factory(
            review_config=review_config,
            sensitive_config=sensitive_config,
            knowledge_agent=dependencies.knowledge_factory(
                knowledge_config=review_config,
                sensitive_config=sensitive_config,
            ),
        ),
        dependencies.agent_fingerprint_values(
            review_config=review_config,
            sensitive_config=sensitive_config,
        ),
    )
    return agent.app, agent.initial_state(
        user_query,
        obs_file_list,
        locale=locale,
    )
