# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Non-secret configuration models used by the Phytomni agents."""

from typing import Annotated, Any, ClassVar, Literal

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

ComputeResourceName = Literal["small", "medium", "large"]


def resolve_compute_resource(
    config: Any,
    analysis_type: str | None = None,
) -> str:
    """Return the configured compute tier, optionally specialized by type.

    Callers must not invent ``small`` / ``medium`` / ``large`` at the
    submit site. Per-analysis overrides live on
    ``COMPUTE_RESOURCE_BY_TYPE``; everything else uses
    ``COMPUTE_RESOURCE``. ``AnalystConfig.RESOURCE`` maps those names to
    cpu/memory. ``APP_ID`` is a Huawei UUID map, not a compute tier.
    """
    by_type = getattr(config, "COMPUTE_RESOURCE_BY_TYPE", None) or {}
    if analysis_type:
        specialized = by_type.get(analysis_type)
        if specialized is not None:
            return specialized
    resource = getattr(config, "COMPUTE_RESOURCE", None)
    if isinstance(resource, str) and resource:
        return resource
    fields = getattr(config, "model_fields", None)
    if isinstance(fields, dict) and "COMPUTE_RESOURCE" in fields:
        default = fields["COMPUTE_RESOURCE"].default
        if isinstance(default, str) and default:
            return default
    raise TypeError("config is missing COMPUTE_RESOURCE")


class ChatConfig(ServerConfig):
    """Configuration settings for chat interactions with a language model."""

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = "phyto-chat"
    TIMEOUT: float = 3000.0
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

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = "phyto-knowledge"
    TIMEOUT: float = 15000.0
    PAGE_NUM: int = 1
    PAGE_SIZE: int = int(_MAX_TOKENS / 512)
    TOP_N: int = int(_MAX_TOKENS / 512)
    FILTER_STRING: str | None = None
    SCOPE: Literal["both", "doc", "keyword"] = "both"
    EXTRA_REPO_IDS: list[str] | None = None
    SCORE_THRESHOLD: float = 0
    RERANK_BATCH_SIZE: int = 128


class DataConfig(KnowledgeConfig):
    """Configuration settings for database query operations."""

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = "phyto-data"
    TIMEOUT: float = 9000.0
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
    """Configuration settings for analysis workflows.

    ``COMPUTE_RESOURCE`` is the default tier name (``small`` here).
    ``RESOURCE`` is the only cpu/memory table: small 1C/4G, medium
    4C/16G, large 16C/48G. ``APP_ID`` maps analysis types to Huawei
    application UUIDs and is not a resource size.
    """

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = None
    TIMEOUT: float = 600.0
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
    COMPUTE_RESOURCE: ComputeResourceName = "small"
    COMPUTE_RESOURCE_BY_TYPE: ClassVar[dict[str, ComputeResourceName]] = {}
    TASK_NAME: str = "analyst-agents-task"
    RESOURCE: dict[str, dict[str, int]] = {
        "small": {"cpu": 1, "memory": 4},
        "medium": {"cpu": 4, "memory": 16},
        "large": {"cpu": 16, "memory": 48},
    }
    APP_ID: Annotated[
        dict[str, str],
        Field(
            default={},
            validation_alias=AliasChoices("APP_ID", "PHYTOMNI_APP_ID"),
            description=(
                "Huawei application UUID map keyed by analysis type. "
                "Not a CPU or memory size; those live on RESOURCE."
            ),
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

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = "phyto-review"
    TIMEOUT: float = 30000.0
    TOP_N: int = int(_MAX_TOKENS / 2048)


class BriefGeneConfig(KnowledgeConfig):
    """Configuration settings for brief gene function reports."""

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = "phyto-brief-gene"
    TIMEOUT: float = 30000.0
    TOP_N: int = int(_MAX_TOKENS / 2048)


class GeneNetworkConfig(AnalystConfig):
    """Configuration settings specific to gene network tasks."""

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


# pylint: disable-next=too-many-ancestors
class DeepGenomeConfig(DataConfig, AnalystConfig):
    """Configuration settings specific to gene function analysis tasks.

    ``SPA_FAQ_URL`` / ``SPA_REPO_ID`` are the species-taxonomy FAQ
    (Latin name to NCBI taxid) used by Evolution and DeepGenome, not a
    web SPA. ``COMPUTE_RESOURCE_BY_TYPE`` raises evolution and protein
    analyses to ``medium``.
    """

    RELAY_TIMEOUT_PROFILE: ClassVar[str | None] = None
    TIMEOUT: float = 600.0
    COMPUTE_RESOURCE_BY_TYPE: ClassVar[dict[str, ComputeResourceName]] = {
        "evolution_analysis": "medium",
        "protein_structure_analysis": "medium",
        "protein_design_analysis": "medium",
    }
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
            description=(
                "Species-taxonomy FAQ repository id for Latin-name "
                "to NCBI taxid lookup. Not a web frontend id."
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
            description=(
                "Species-taxonomy FAQ URL template. Format with "
                "repo_id=SPA_REPO_ID. Not a web SPA origin."
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
    COMPUTE_RESOURCE_BY_TYPE: ClassVar[dict[str, ComputeResourceName]] = {
        "protein_design_analysis": "medium",
        "protein_structure_analysis": "medium",
    }


class InSilicoResearchConfig(AnalystConfig):
    """Configuration settings specific to in-silico research tasks.

    Defaults ``COMPUTE_RESOURCE`` to ``small``. Research children
    start at small and may relaunch on memory-class failure; they
    do not inherit a medium floor.
    """

    COMPUTE_RESOURCE: ComputeResourceName = "small"


class EnvironmentConfig(AnalystConfig):
    """Configuration settings specific to environment tasks.

    Defaults ``COMPUTE_RESOURCE`` to ``large``. Environment is an
    Analyst-backed subgraph, not an MCP tool.
    """

    COMPUTE_RESOURCE: ComputeResourceName = "large"
    ENVIRONMENT_DATA: str = str(PRE_PREPARED_DATA_PATH)
    REGION_CODE: str = str(PRE_PREPARED_REGION_PATH)
