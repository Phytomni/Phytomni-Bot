# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Required non-secret configuration for outbound logical request pools."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class OutboundConfig(BaseSettings):
    """Define the process-local concurrency budget for each service family."""

    OUTBOUND_LLM_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_LLM_CONCURRENCY", "PHYTOMNI_OUTBOUND_LLM_CONCURRENCY"
        ),
    )
    OUTBOUND_RETRIEVAL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_RETRIEVAL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RETRIEVAL_CONCURRENCY",
        ),
    )
    OUTBOUND_RERANK_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_RERANK_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RERANK_CONCURRENCY",
        ),
    )
    OUTBOUND_NL2SQL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_NL2SQL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_NL2SQL_CONCURRENCY",
        ),
    )
    OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
        ),
    )
    OUTBOUND_ANALYSIS_STATUS_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
        ),
    )
    OUTBOUND_IAM_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_IAM_CONCURRENCY", "PHYTOMNI_OUTBOUND_IAM_CONCURRENCY"
        ),
    )
    OUTBOUND_SPA_FAQ_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_SPA_FAQ_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_SPA_FAQ_CONCURRENCY",
        ),
    )
    OUTBOUND_BI_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_BI_CONCURRENCY", "PHYTOMNI_OUTBOUND_BI_CONCURRENCY"
        ),
    )
    OUTBOUND_OBS_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_OBS_CONCURRENCY", "PHYTOMNI_OUTBOUND_OBS_CONCURRENCY"
        ),
    )
    OUTBOUND_RELAY_CONTROL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_RELAY_CONTROL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RELAY_CONTROL_CONCURRENCY",
        ),
    )
    OUTBOUND_INTEROP_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        validation_alias=AliasChoices(
            "OUTBOUND_INTEROP_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_INTEROP_CONCURRENCY",
        ),
    )
    OUTBOUND_POOL_WAIT_WARN_SECONDS: float = Field(
        init=False,
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices(
            "OUTBOUND_POOL_WAIT_WARN_SECONDS",
            "PHYTOMNI_OUTBOUND_POOL_WAIT_WARN_SECONDS",
        ),
    )


__all__ = ["OutboundConfig"]
