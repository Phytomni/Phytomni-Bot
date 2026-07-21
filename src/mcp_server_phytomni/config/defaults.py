# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Default non-secret configuration for Phytomni agents.

Classes: ServerConfig, ChatConfig, KnowledgeConfig, DataConfig, AnalystConfig,
    ReviewConfig, BriefGeneConfig, GeneNetworkConfig, DeepGenomeConfig,
    DigitalDesignConfig, InSilicoResearchConfig, EnvironmentConfig, ApiConfig,
    SpeciesDataIndex, RegionMap, PromptTemplates.
"""

from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    Field,
    RootModel,
    field_validator,
)

from .api_limits import ApiLimitsConfig as _ApiLimitsConfig
from .models.api import ApiConfig as _ApiConfig
from .models.base import (
    _MAX_TOKENS,
    DOWNLOAD_PATH,
    PRE_PREPARED_DATA_PATH,
    PRE_PREPARED_REGION_PATH,
    ServerConfig,
    _require_non_empty_endpoint,
)
from .models.base import (
    PARENT_PATH as _PARENT_PATH,
)
from .models.base import (
    PROMPT_PATH as _PROMPT_PATH,
)
from .models.base import (
    TEMP_PATH as _TEMP_PATH,
)
from .required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
)
from .required_env import (
    SERVER_REQUIRED_ENDPOINT_FIELDS as _SERVER_REQUIRED_ENDPOINT_FIELDS,
)

ApiLimitsConfig = _ApiLimitsConfig
ApiConfig = _ApiConfig
PARENT_PATH = _PARENT_PATH
PROMPT_PATH = _PROMPT_PATH
TEMP_PATH = _TEMP_PATH
SERVER_REQUIRED_ENDPOINT_FIELDS = _SERVER_REQUIRED_ENDPOINT_FIELDS


class ChatConfig(ServerConfig):
    """Configuration settings for chat interactions with a language model.

    Inherits settings from `ServerConfig`.

    Attributes:
        USER (str): User identifier for API interactions, particularly for
            chat or LLM services.
        TEMPERATURE (float): Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        TOP_P (float): Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to `TOP_P`.
        MAX_TOKENS (int): Maximum number of tokens to generate in language
            model responses.
        PRESENCE_PENALTY (float): Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        FREQUENCY_PENALTY (float): Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        N (int): Number of completion choices to generate for each input.
        REASONING_EFFORT (Literal['low', 'medium', 'high']): Specifies the
            level of reasoning effort for the language model.
        RESPONSE_FORMAT (Dict[str, Union[str, Dict]]): Desired response format
            from the language model. For example, `{'type': 'json_object'}`
            to request a JSON response.
        STREAM (bool): Flag to enable or disable streaming of responses from
            the language model. If True, responses are sent as a series of
            events.
    """

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
    """Configuration settings for knowledge retrieval and document search.

    Inherits settings from `ChatConfig`.

    Attributes:
        PAGE_NUM (int): Page number for paginated results from retrieval
            services.
        PAGE_SIZE (int): Number of items per page for paginated results from
            retrieval services.
        TOP_N (int): Number of top-scoring results to retrieve or consider.
        FILTER_STRING (Optional[str]): Optional filter criteria string for
            metadata filtering during retrieval.
        SCOPE (Literal['both', 'doc', 'keyword']): Scope of search for
            retrieval operations. 'both' searches documents and keywords,
            'doc' searches only documents, 'keyword' searches only keywords.
        EXTRA_REPO_IDS (Optional[List[str]]): Optional list of additional
            repository IDs to include in retrieval.
        SCORE_THRESHOLD (float): Minimum relevance score threshold for
            retrieved items. Results below this threshold are typically
            discarded.
        RERANK_BATCH_SIZE (int): Batch size for reranking operations, if
            reranking is applied to retrieved documents.
        RERANK_CONCURRENCY (int): Max concurrent rerank HTTP requests per
            process event loop. 0 or negative disables throttling.
    """

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
    """Configuration settings for database query operations.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        NEED_INSIGHT (bool): Flag indicating whether to generate insights from
            database queries.
        SIMPLIFY_RESPONSE (bool): Flag indicating whether to simplify the
            structure of database query responses.
        DIALOG_ID (str): Conversation ID for multi-turn context.
        DATA_REPO_ID (str): The ID of the primary knowledge repository to
            search for Data-Agent RAG functionality.
        DATA_PAGE_SIZE (int): Number of items per page for paginated results
            from retrieval services specific to Data-Agent operations.
    """

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

    # Deployment-specific UUID; required as an env var (no default).
    _validate_data_repo_id = field_validator(
        *DATA_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)


class AnalystConfig(KnowledgeConfig):
    """Configuration settings for analysis workflows.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        OUTPUT_DIR (str): Output directory path for storing results of
            analysis or operations (e.g., an OBS path).
        EXECUTE_CODE (bool): Flag indicating whether code execution is
            permitted during an analysis operation.
        POLL_INTERVAL (float): Interval in seconds for polling the status of
            long-running analysis tasks.
        MAX_POLL (float): Maximum duration in seconds for polling the status
            of long-running analysis tasks.
        ANALYSIS_JOB_TIMEOUT (float): Maximum duration in seconds sent to the
            remote analysis platform for one submitted job.
        COMPUTE_RESOURCE: compute resource type.
        TASK_NAME: task name in ai4s platform.
        APP_ID: app id in difference compute resource
        RESOURCE: cpu and memory information in difference compute resource.
        PLAN_MIN_SCORE (int): Minimum critic score the analysis plan
            must reach. 0 (default) disables the gate and preserves the
            current force-approve-on-retry-exhaustion behavior; when > 0
            the plan-check loop fails loudly via McpError if retries
            exhaust below this score. Inherited by
            InSilicoResearchConfig.
    """

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

    # Deployment-specific UUID; required as an env var (no default).
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
    # Deployment-specific compute-tier app ids; ship as a JSON string
    # env value (e.g. ``PHYTOMNI_APP_ID='{"small":"<uuid>",...}'``)
    # that pydantic-settings parses into ``Dict[str, str]``. Mirrors the
    # REPO_ID_DICT pattern: ``default={}`` (not ``default_factory``) so
    # mypy without the pydantic plugin sees the field as defaulted,
    # while Pydantic v2 deep-copies the literal per instance.
    APP_ID: Annotated[
        dict[str, str],
        Field(
            default={},
            validation_alias=AliasChoices("APP_ID", "PHYTOMNI_APP_ID"),
        ),
    ] = {}

    # Deployment-specific UUID map; required as an env var (no default).
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
    """Configuration settings for review generation or related tasks.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        TOP_N (int): Number of top-scoring results to retrieve or consider
            specifically for review purposes.
    """

    TOP_N: int = int(_MAX_TOKENS / 2048)


class BriefGeneConfig(KnowledgeConfig):
    """Configuration settings for brief gene function reports.

    This agent combines direct GaussDB annotation lookup with literature
    retrieval, so it inherits the knowledge retrieval and chat defaults.

    Attributes:
        TOP_N: Maximum number of literature retrieval results to keep.
    """

    TOP_N: int = int(_MAX_TOKENS / 2048)


class GeneNetworkConfig(AnalystConfig):
    """Configuration settings specific to gene network tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        DEEPGENOME_DATA: Static species metadata file used for task prompts.
    """

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class DeepGenomeConfig(DataConfig, AnalystConfig):
    """Configuration settings specific to gene function analysis tasks.

    Inherits settings from both `DataConfig` and `AnalystConfig`.

    Attributes:
        MAX_CONCURRENCY (int): Maximum number of concurrent operations allowed
            for tasks related to gene function analysis.
    """

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

    # Deployment-specific endpoints / UUIDs / BI host; all required
    # as env vars so a customer image never ships with another
    # tenant's URL or repo id baked in as a default.
    _validate_dg_endpoints = field_validator(
        *DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)


class DigitalDesignConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        DEEPGENOME_DATA: Static species metadata file used for task prompts.
    """

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class InSilicoResearchConfig(AnalystConfig):
    """Configuration settings specific to in silico research tasks.

    Inherits settings from `AnalystConfig`.
    """


class EnvironmentConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        ENVIRONMENT_DATA: Static environment metadata file.
        REGION_CODE: Static region metadata file.
    """

    ENVIRONMENT_DATA: str = str(PRE_PREPARED_DATA_PATH)
    REGION_CODE: str = str(PRE_PREPARED_REGION_PATH)


SpeciesEntryValue = str | dict[str, str]


class SpeciesDataIndex(
    RootModel[dict[str, dict[str, dict[str, SpeciesEntryValue]]]]
):
    """Validation schema for species_data_list.json.

    The file follows the shape
    ``{analysis_type: {species_name: {key: leaf}}}`` where ``leaf`` is
    either an OBS file description string or a sub-dict mapping further
    keys (e.g. ``cultivars``, ``tissues``) to ``{file_path: description}``.
    """

    def analysis_types(self) -> list[str]:
        """Return the top-level analysis type keys.

        Returns:
            List of analysis-type keys present in the validated index.
        """
        return list(self.root.keys())

    def species_for(self, analysis_type: str) -> list[str]:
        """Return the species keys configured under ``analysis_type``.

        Args:
            analysis_type: Analysis type key (e.g. ``"evolution_analysis"``).

        Returns:
            List of species keys under the requested analysis type.
        """
        return list(self.root[analysis_type].keys())


class RegionMap(RootModel[dict[str, dict[str, dict[str, str]]]]):
    """Validation schema for region_map.json.

    The file maps {province: {city: {district: region_code}}}; every leaf
    value is a pipe-delimited string like ``"310000|310000|310114"``.
    """

    def provinces(self) -> list[str]:
        """Return the top-level province keys.

        Returns:
            List of province keys present in the validated map.
        """
        return list(self.root.keys())

    def cities_for(self, province: str) -> list[str]:
        """Return the city keys configured under ``province``.

        Args:
            province: Province key (e.g. ``"北京市"``).

        Returns:
            List of city keys under the requested province.
        """
        return list(self.root[province].keys())


PromptLeaf = str | dict[str, str]


class PromptTemplates(RootModel[dict[str, dict[str, PromptLeaf]]]):
    """Validation schema for .prompts.yaml.

    The file follows the shape ``{section: {key: leaf}}`` where
    ``section`` is one of ``system``, ``template``, or ``user``, and
    ``leaf`` is either a prompt template string (depth-2 entries) or a
    sub-dict of ``{name: template}`` entries (depth-3 entries used for
    nested prompt paths such as ``user/environment/get_code_query``).
    """

    def sections(self) -> list[str]:
        """Return the top-level prompt section keys.

        Returns:
            List of section keys present in the validated templates.
        """
        return list(self.root.keys())

    def keys_for(self, section: str) -> list[str]:
        """Return the prompt keys configured under ``section``.

        Args:
            section: Top-level section key (e.g. ``"system"``).

        Returns:
            List of prompt keys under the requested section.
        """
        return list(self.root[section].keys())
