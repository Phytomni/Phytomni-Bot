# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Configurable safety limits shared by the HTTP API and graph surfaces."""

from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings


def _api_bounded_int(
    default: int,
    *,
    ge: int,
    le: int,
    env_name: str,
) -> Any:
    """Build a bounded API integer with both environment aliases."""
    return Field(
        default=default,
        ge=ge,
        le=le,
        validation_alias=AliasChoices(env_name, f"PHYTOMNI_{env_name}"),
    )


class ApiLimitsConfig(BaseSettings):
    """Operator-tunable upper bounds with conservative safe defaults."""

    MEMORY_MAX_ITEMS: int = _api_bounded_int(
        100, ge=1, le=10_000, env_name="MEMORY_MAX_ITEMS"
    )
    MEMORY_MAX_CONTENT_BYTES: int = _api_bounded_int(
        16 * 1024, ge=1, le=16 * 1024, env_name="MEMORY_MAX_CONTENT_BYTES"
    )
    MEMORY_MAX_TOTAL_BYTES: int = _api_bounded_int(
        1024 * 1024,
        ge=1,
        le=16 * 1024 * 1024,
        env_name="MEMORY_MAX_TOTAL_BYTES",
    )
    MEMORY_MAX_RETRIEVAL: int = _api_bounded_int(
        20, ge=1, le=1000, env_name="MEMORY_MAX_RETRIEVAL"
    )
    MEMORY_GRAPH_MAX_BYTES: int = _api_bounded_int(
        64 * 1024, ge=1, le=1024 * 1024, env_name="MEMORY_GRAPH_MAX_BYTES"
    )
    INTEROP_MAX_TARGETS: int = _api_bounded_int(
        64, ge=1, le=256, env_name="INTEROP_MAX_TARGETS"
    )
    INTEROP_CACHE_MAX_ENTRIES: int = _api_bounded_int(
        256, ge=1, le=4096, env_name="INTEROP_CACHE_MAX_ENTRIES"
    )
    A2A_MAX_HISTORY_MESSAGES: int = _api_bounded_int(
        32, ge=0, le=256, env_name="A2A_MAX_HISTORY_MESSAGES"
    )
    A2A_MAX_ARTIFACT_BYTES: int = _api_bounded_int(
        256 * 1024,
        ge=1024,
        le=16 * 1024 * 1024,
        env_name="A2A_MAX_ARTIFACT_BYTES",
    )

    @model_validator(mode="after")
    def _validate_memory_bounds(self) -> "ApiLimitsConfig":
        """Keep configured memory retrieval within namespace capacity."""
        if self.MEMORY_MAX_RETRIEVAL > self.MEMORY_MAX_ITEMS:
            raise ValueError(
                "MEMORY_MAX_RETRIEVAL must not exceed MEMORY_MAX_ITEMS"
            )
        return self


__all__ = ["ApiLimitsConfig"]
