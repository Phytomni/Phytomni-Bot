# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Required non-secret configuration for outbound logical request pools."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class OutboundConfig(BaseSettings):
    """Define the process-local concurrency budget for each service family.

    Each field is the capacity of one ``OutboundPoolName``. Expert
    routing and the dispatched agent share the LLM pool, so one routed
    HTTP turn can hold two LLM leases. SPA_FAQ is the species-taxonomy
    lookup, not a web frontend.
    """

    OUTBOUND_LLM_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of the shared LLM completion pool.",
        validation_alias=AliasChoices(
            "OUTBOUND_LLM_CONCURRENCY", "PHYTOMNI_OUTBOUND_LLM_CONCURRENCY"
        ),
    )
    OUTBOUND_RETRIEVAL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of the knowledge retrieval pool.",
        validation_alias=AliasChoices(
            "OUTBOUND_RETRIEVAL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RETRIEVAL_CONCURRENCY",
        ),
    )
    OUTBOUND_RERANK_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of the rerank pool.",
        validation_alias=AliasChoices(
            "OUTBOUND_RERANK_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RERANK_CONCURRENCY",
        ),
    )
    OUTBOUND_NL2SQL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of the NL2SQL / Data-agent pool.",
        validation_alias=AliasChoices(
            "OUTBOUND_NL2SQL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_NL2SQL_CONCURRENCY",
        ),
    )
    OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of analysis-platform submit calls.",
        validation_alias=AliasChoices(
            "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
        ),
    )
    OUTBOUND_ANALYSIS_STATUS_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of analysis-platform status polls.",
        validation_alias=AliasChoices(
            "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
        ),
    )
    OUTBOUND_IAM_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of IAM token fetches.",
        validation_alias=AliasChoices(
            "OUTBOUND_IAM_CONCURRENCY", "PHYTOMNI_OUTBOUND_IAM_CONCURRENCY"
        ),
    )
    OUTBOUND_SPA_FAQ_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description=(
            "Capacity of species-taxonomy FAQ lookups (Latin name "
            "to NCBI taxid), not a web SPA."
        ),
        validation_alias=AliasChoices(
            "OUTBOUND_SPA_FAQ_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_SPA_FAQ_CONCURRENCY",
        ),
    )
    OUTBOUND_BI_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of GaussDB / BI query calls.",
        validation_alias=AliasChoices(
            "OUTBOUND_BI_CONCURRENCY", "PHYTOMNI_OUTBOUND_BI_CONCURRENCY"
        ),
    )
    OUTBOUND_OBS_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of OBS object-storage calls.",
        validation_alias=AliasChoices(
            "OUTBOUND_OBS_CONCURRENCY", "PHYTOMNI_OUTBOUND_OBS_CONCURRENCY"
        ),
    )
    OUTBOUND_RELAY_CONTROL_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of relay control-plane calls.",
        validation_alias=AliasChoices(
            "OUTBOUND_RELAY_CONTROL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RELAY_CONTROL_CONCURRENCY",
        ),
    )
    OUTBOUND_INTEROP_CONCURRENCY: int = Field(
        init=False,
        ge=0,
        description="Capacity of operator-owned Interop HTTP targets.",
        validation_alias=AliasChoices(
            "OUTBOUND_INTEROP_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_INTEROP_CONCURRENCY",
        ),
    )
    OUTBOUND_POOL_WAIT_WARN_SECONDS: float = Field(
        init=False,
        gt=0,
        allow_inf_nan=False,
        description="Emit a wait warning after this many seconds.",
        validation_alias=AliasChoices(
            "OUTBOUND_POOL_WAIT_WARN_SECONDS",
            "PHYTOMNI_OUTBOUND_POOL_WAIT_WARN_SECONDS",
        ),
    )


__all__ = ["OutboundConfig"]
