# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Non-secret configuration models used by the Phytomni agents."""

from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator

from ..required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
)
from .base import (
    _MAX_TOKENS,
    DOWNLOAD_PATH,
    PRE_PREPARED_DATA_PATH,
    PRE_PREPARED_REGION_PATH,
    ServerConfig,
    _require_non_empty_endpoint,
)


class ChatConfig(ServerConfig):
    """Configuration settings for chat interactions with a language model."""

    USER: str = "test"
    TEMPERATURE: float = 0.3
    TOP_P: float = 1
    PRESENCE_PENALTY: float = 0
    FREQUENCY_PENALTY: float = 0
    N: int = 1
    REASONING_EFFORT: Literal["low", "medium", "high"] = "high"
    RESPONSE_FORMAT: dict[str, str | dict] = {"type": "json_object"}
    STREAM: bool = False


class KnowledgeConfig(ChatConfig):
    """Configuration settings for retrieval and document search."""

    PAGE_NUM: int = 1
    PAGE_SIZE: int = int(_MAX_TOKENS / 512)
    TOP_N: int = int(_MAX_TOKENS / 512)
    FILTER_STRING: str | None = None
    SCOPE: Literal["both", "doc", "keyword"] = "both"
    EXTRA_REPO_IDS: list[str] | None = None
    SCORE_THRESHOLD: float = 0
    RERANK_BATCH_SIZE: int = 128
    RERANK_CONCURRENCY: Annotated[
        int,
        Field(
            default=16,
            validation_alias=AliasChoices(
                "RERANK_CONCURRENCY", "PHYTOMNI_RERANK_CONCURRENCY"
            ),
        ),
    ] = 16


class DataConfig(KnowledgeConfig):
    """Configuration settings for database query operations."""

    NEED_INSIGHT: bool = False
    SIMPLIFY_RESPONSE: bool = True
    DIALOG_ID: str = ""
    DATA_REPO_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "DATA_REPO_ID", "PHYTOMNI_DATA_REPO_ID"
            ),
        ),
    ] = ""
    DATA_PAGE_SIZE: int = 3

    _validate_data_repo_id = field_validator(
        *DATA_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)


class AnalystConfig(KnowledgeConfig):
    """Configuration settings for analysis workflows."""

    TOOL_REPO_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "TOOL_REPO_ID", "PHYTOMNI_TOOL_REPO_ID"
            ),
        ),
    ] = ""
    TOOL_PAGE_NUM: int = 1
    TOOL_PAGE_SIZE: int = 2

    _validate_tool_repo_id = field_validator(
        *ANALYST_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)
    OUTPUT_DIR: str = "/obs/phytomni/agent_data/test/output"
    COMPUTE_RESOURCE: Literal["small", "medium", "large"] = "small"
    TASK_NAME: str = "analyst-agents-task"
    RESOURCE: dict[str, dict[str, int]] = {
        "small": {"cpu": 1, "memory": 4},
        "medium": {"cpu": 4, "memory": 8},
        "large": {"cpu": 16, "memory": 48},
    }
    APP_ID: Annotated[
        dict[str, str],
        Field(
            default={},
            validation_alias=AliasChoices("APP_ID", "PHYTOMNI_APP_ID"),
        ),
    ] = {}

    _validate_app_id = field_validator("APP_ID", mode="after")(
        _require_non_empty_endpoint
    )

    EXECUTE_CODE: bool = True
    USER_ID: str = ""
    CREATE_DIR: bool = True
    DOWNLOAD_PATH: str = str(DOWNLOAD_PATH)
    DOWNLOAD_MARKER: str | None = None
    DOWNLOAD_MAX_KEYS: int = 1000
    IF_DOWNLOAD_ALL: bool = True
    TARGET_FILE_FEATURE: list[str] = [""]
    PRE_PREPARED_DATA_PATH: str = str(PRE_PREPARED_DATA_PATH)
    POLL_INTERVAL: float = 300
    MAX_POLL: float = 86400
    ANALYSIS_JOB_TIMEOUT: float = 86400
    PLAN_MIN_SCORE: int = 0


class ReviewConfig(KnowledgeConfig):
    """Configuration settings for review generation."""

    TOP_N: int = int(_MAX_TOKENS / 2048)


class BriefGeneConfig(KnowledgeConfig):
    """Configuration settings for brief gene function reports."""

    TOP_N: int = int(_MAX_TOKENS / 2048)


class GeneNetworkConfig(AnalystConfig):
    """Configuration settings specific to gene network tasks."""

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class DeepGenomeConfig(DataConfig, AnalystConfig):
    """Configuration settings specific to gene function analysis tasks."""

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)
    DEEPGENOME_OUT: str = str(DOWNLOAD_PATH)
    BATCH: bool = True
    EPIC_TYPE: str = "6mA"
    PROTOCOL_REPO_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "PROTOCOL_REPO_ID", "PHYTOMNI_PROTOCOL_REPO_ID"
            ),
        ),
    ] = ""
    PROTOCOL_PAGE_SIZE: int = 128
    SPA_REPO_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "SPA_REPO_ID", "PHYTOMNI_SPA_REPO_ID"
            ),
        ),
    ] = ""
    SPA_FAQ_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "SPA_FAQ_URL", "PHYTOMNI_SPA_FAQ_URL"
            ),
        ),
    ] = ""

    _validate_dg_endpoints = field_validator(
        *DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)


class DigitalDesignConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks."""

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class InSilicoResearchConfig(AnalystConfig):
    """Configuration settings specific to in-silico research tasks."""


class EnvironmentConfig(AnalystConfig):
    """Configuration settings specific to environment tasks."""

    ENVIRONMENT_DATA: str = str(PRE_PREPARED_DATA_PATH)
    REGION_CODE: str = str(PRE_PREPARED_REGION_PATH)
