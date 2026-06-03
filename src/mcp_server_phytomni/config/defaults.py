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

from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import (
    AliasChoices,
    Field,
    RootModel,
    SecretStr,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings

from .relay_mode import relay_mode_enabled


def _require_non_empty_endpoint(value, info: ValidationInfo):
    """Reject an empty deployment field with an env-name-aware message.

    Production / customer images bake the per-deployment value into
    the encrypted ``.env`` envelope; every test path adds the value to
    ``tests/conftest.py:_TEST_ENV``. An empty default would otherwise
    let a misconfigured deployment silently fall through to a 404 /
    DNS-resolve error (string endpoints) or a KeyError (dict tokens
    map) at first agent call, which is much harder to diagnose than a
    startup ``ValidationError`` naming the missing env var.

    Args:
        value: Resolved field value from ``BaseSettings`` env
            resolution. ``str`` for URLs / repo ids; ``dict`` for the
            REPO_ID_DICT-style tokens map; both share the same
            falsy-on-empty contract.
        info: Pydantic validation context; ``info.field_name`` carries
            the field label used in the error message so the operator
            sees the env-var they need to set.

    Returns:
        The non-empty value unchanged.

    Raises:
        ValueError: When the value is empty after env resolution.
    """
    # Customer relay mode boots with only ``PHYTOMNI_RELAY_*`` set and
    # has no operator endpoints/UUIDs; skip the non-empty contract so a
    # relay-mode child Bot does not raise during its import-time config
    # construction. Normal mode keeps the strict fail-fast behavior.
    if relay_mode_enabled():
        return value
    if not value:
        raise ValueError(
            f"{info.field_name} is required; set the {info.field_name} "
            f"or PHYTOMNI_{info.field_name} environment variable."
        )
    return value


# Single source of truth for the ``ServerConfig`` env-required field
# set; the field_validator below and ``tests/unit/config/test_defaults``
# both consume this tuple so adding a new required field updates the
# validator and the negative-test parametrize in one place.
SERVER_REQUIRED_ENDPOINT_FIELDS = (
    "TOKEN_URL",
    "RETRIEVE_URL",
    "RERANK_URL",
    "DATABASE_URL",
    "ANALYSIS_URL",
    "REPO_ID",
    "REPO_ID_DICT",
    "WORKSPACE_ID",
    "SUBJECT_ID",
    "OBS_SERVER",
)

_MAX_TOKENS = 65536
PARENT_PATH = Path(__file__).parent.parent
PROMPT_PATH = PARENT_PATH / "config/.prompts.yaml"
PRE_PREPARED_DATA_PATH = PARENT_PATH / "config/species_data_list.json"
PRE_PREPARED_REGION_PATH = PARENT_PATH / "config/region_map.json"
DOWNLOAD_PATH = PARENT_PATH / ".out"
TEMP_PATH = PARENT_PATH / ".temp"


class ServerConfig(BaseSettings):
    """Configuration settings for server-level parameters and service
        endpoints.

    Attributes:
        PROMPT_FILE (str): Path to the main prompt template file.
        PROMPT_PATH (str): Default path or key within the prompt file to
            retrieve specific system prompts.
        TIMEOUT (float): General request timeout in seconds for API calls.
        RETRIABLE_CODES (List[int]): List of HTTP status codes that trigger
            retries for API calls.
        MAX_RETRIES (int): Maximum number of retry attempts for API calls.
        TOKEN_URL (str): URL for obtaining authentication tokens.
        REGION (str): Cloud service region.
        RETRIEVE_URL (str): URL for the document retrieval service.
        RERANK_URL (str): URL for the document reranking service.
        DATABASE_URL (str): URL for the database query service (e.g., NLQ).
        ANALYSIS_URL (str): URL for the workflow analysis service.
        ANALYSIS_REGION (str): Analyst agents cloud service region.
        REPO_ID (str): A default or primary repository identifier.
        REPO_ID_DICT (Dict[str, int]): Dictionary mapping repository IDs to
            associated integer values (e.g., page sizes or token limits).
        WORKSPACE_ID (str): Identifier for the workspace.
        SUBJECT_ID (str): Identifier for the database subject or schema.
        POLL_INTERVAL (float): Interval in seconds for polling the status of
            long-running tasks.
        MAX_POLL (float): Maximum duration in seconds for polling the status
            of long-running tasks.
    """

    MAX_TOKENS: int = _MAX_TOKENS

    PROMPT_FILE: str = str(PROMPT_PATH)
    PROMPT_PATH: str = "system/ai4ps"

    TEMP_DIR: str = str(TEMP_PATH)
    TIMEOUT: float = 600
    RETRIABLE_CODES: List[int] = [429, 500, 502, 503, 504]
    MAX_RETRIES: int = 5
    MAX_CONCURRENCY: int = 4
    MAX_WORKERS: int = 4

    TOKEN_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("TOKEN_URL", "PHYTOMNI_TOKEN_URL"),
        ),
    ] = ""
    REGION: str = "cn-southwest-2"

    RETRIEVE_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "RETRIEVE_URL", "PHYTOMNI_RETRIEVE_URL"
            ),
        ),
    ] = ""
    RERANK_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("RERANK_URL", "PHYTOMNI_RERANK_URL"),
        ),
    ] = ""
    DATABASE_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "DATABASE_URL", "PHYTOMNI_DATABASE_URL"
            ),
        ),
    ] = ""
    ANALYSIS_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "ANALYSIS_URL", "PHYTOMNI_ANALYSIS_URL"
            ),
        ),
    ] = ""
    ANALYSIS_REGION: str = "cn-east-3"

    REPO_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("REPO_ID", "PHYTOMNI_REPO_ID"),
        ),
    ] = ""
    # ``default={}`` rather than ``default_factory=dict`` so mypy
    # (without the pydantic plugin) sees the field as defaulted;
    # Pydantic v2 deep-copies dict literals per instance.
    REPO_ID_DICT: Annotated[
        Dict[str, int],
        Field(
            default={},
            validation_alias=AliasChoices(
                "REPO_ID_DICT", "PHYTOMNI_REPO_ID_DICT"
            ),
        ),
    ] = {}

    WORKSPACE_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "WORKSPACE_ID", "PHYTOMNI_WORKSPACE_ID"
            ),
        ),
    ] = ""
    SUBJECT_ID: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("SUBJECT_ID", "PHYTOMNI_SUBJECT_ID"),
        ),
    ] = ""

    OBS_SERVER: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("OBS_SERVER", "PHYTOMNI_OBS_SERVER"),
        ),
    ] = ""
    BUCKET_NAME: str = "phytomni"
    PART_SIZE: int = 16777216
    TASK_NUM: int = 8

    POLL_INTERVAL: float = 300
    MAX_POLL: float = 86400

    # TLS deployment knobs read by common.httpx_client.get_async_client.
    # Default verify=True is the safe production posture; on-prem and
    # dev environments override via PHYTOMNI_TLS_VERIFY=0 (or supply a
    # PHYTOMNI_CA_BUNDLE path when the corporate CA is not in the
    # system trust store). The aliases let docs use the PHYTOMNI_*
    # naming convention while keeping internal field names short.
    TLS_VERIFY: Annotated[
        bool,
        Field(
            default=True,
            validation_alias=AliasChoices("TLS_VERIFY", "PHYTOMNI_TLS_VERIFY"),
        ),
    ] = True
    CA_BUNDLE: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("CA_BUNDLE", "PHYTOMNI_CA_BUNDLE"),
        ),
    ] = None

    # Consumer-side opt-in: when True, every agent that invokes chat
    # (knowledge / data / analyst / review / brief_gene / design /
    # network / research / environment / evolution / deep_genome)
    # routes its chat call through the chat agent's compiled subgraph
    # via ``adapter_node(chat_subgraph)`` instead of the legacy direct
    # ``phyto_chat`` function call. Default ``False`` preserves
    # pre-Phase-6 behavior; flip to ``True`` per deployment once
    # parent-graph composition has been validated end-to-end.
    # Inherited by every consumer agent config that subclasses
    # ``ServerConfig`` (every ``*Config`` except ``ApiConfig``, which
    # is an independent ``BaseSettings`` subclass). The ``AliasChoices``
    # wrapper mirrors every endpoint field above so customer images
    # that opt-in via ``PHYTOMNI_USE_CHAT_SUBGRAPH=true`` route to the
    # same field as the unprefixed form (the env-prefix promise in
    # ``docs/configuration.md`` + ``.env.example`` must hold for the
    # Step 6.6 default-True rollback knob to work in production).
    USE_CHAT_SUBGRAPH: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices(
                "USE_CHAT_SUBGRAPH", "PHYTOMNI_USE_CHAT_SUBGRAPH"
            ),
        ),
    ] = False

    # ``USE_KNOWLEDGE_SUBGRAPH`` — when ``True``, consumer agents
    # (analyst / data / review retrieve_node) invoke a KnowledgeAgent
    # subgraph through ``adapter_node`` instead of calling the
    # ``multi_retrieve`` / ``retrieve`` helpers inline. Review
    # additionally requires ``USE_CHAT_SUBGRAPH=True`` (the Send-
    # dispatch retrieve triad lives only inside review's
    # ``_wire_chat_subgraph`` branch). Default ``False`` preserves
    # pre-Phase-6 behavior; flip to ``True`` per deployment after
    # parent-graph composition is validated.
    # Inherited by every consumer-side config that subclasses
    # ``ServerConfig``. ``AliasChoices`` mirrors the endpoint-field
    # pattern from inception so the ``PHYTOMNI_USE_KNOWLEDGE_SUBGRAPH``
    # env form routes to the same field as the unprefixed form (the
    # AF-NEW-5 fix lesson — Steps 6.2-6.5 flag introductions all use
    # this shape).
    USE_KNOWLEDGE_SUBGRAPH: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices(
                "USE_KNOWLEDGE_SUBGRAPH",
                "PHYTOMNI_USE_KNOWLEDGE_SUBGRAPH",
            ),
        ),
    ] = False

    # Customer relay-mode client switches (distinct from the operator-
    # side ``ApiConfig.RELAY_ENABLED``). When ``RELAY_MODE`` is True a
    # child Bot routes its non-OBS external dependencies through the
    # upstream relay API at ``RELAY_BASE_URL`` instead of holding the
    # operator endpoints/secrets, and gates the import-time validator
    # fork via ``relay_mode_enabled()`` (in ``config/relay_mode.py``).
    # Inherited by every ``ServerConfig`` subclass so each agent's
    # HTTP-boundary helpers can branch on relay mode.
    RELAY_MODE: bool = Field(
        default=False,
        validation_alias=AliasChoices("RELAY_MODE", "PHYTOMNI_RELAY_MODE"),
    )
    RELAY_BASE_URL: str = Field(
        default="",
        validation_alias=AliasChoices(
            "RELAY_BASE_URL", "PHYTOMNI_RELAY_BASE_URL"
        ),
    )

    # Deployment-specific endpoints + UUIDs are externalised with
    # empty defaults so a misconfigured customer image fails fast
    # with a ``ValidationError`` naming the missing env var, rather
    # than baking a per-deployment IP / customer UUID into the wheel
    # or Docker layer. The validator is shared with every subclass
    # that inherits these fields (``KnowledgeConfig``, ``DataConfig``,
    # ``AnalystConfig``, ...). ``REPO_ID_DICT`` accepts a JSON-string
    # env value (e.g. ``PHYTOMNI_REPO_ID_DICT='{"uuid":N,...}'``)
    # which pydantic-settings parses into ``Dict[str, int]``
    # automatically; the same pattern covers ``APP_ID`` on
    # ``AnalystConfig`` (``Dict[str, str]``).
    _validate_server_endpoints = field_validator(
        *SERVER_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)

    @field_validator("RELAY_BASE_URL", mode="after")
    @classmethod
    def _normalize_relay_base_url(cls, value: str) -> str:
        """Strip a trailing slash and require the URL in relay mode.

        The relay client appends ``/v1/relay/...`` paths, so a stored
        trailing slash would yield a double slash upstream. In relay
        mode an empty base URL is a fatal misconfiguration (every
        forwarded dependency would target an empty host), so fail fast;
        outside relay mode the field is unused and stays optional.
        """
        normalized = value.rstrip("/")
        if relay_mode_enabled() and not normalized:
            raise ValueError(
                "RELAY_BASE_URL is required in relay mode; set the "
                "RELAY_BASE_URL or PHYTOMNI_RELAY_BASE_URL environment "
                "variable."
            )
        return normalized


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
    RESPONSE_FORMAT: Dict[str, Union[str, Dict]] = {"type": "json_object"}
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
    """

    PAGE_NUM: int = 1
    PAGE_SIZE: int = int(_MAX_TOKENS / 512)
    TOP_N: int = int(_MAX_TOKENS / 512)
    FILTER_STRING: Optional[str] = None
    SCOPE: Literal["both", "doc", "keyword"] = "both"
    EXTRA_REPO_IDS: Optional[List[str]] = None
    SCORE_THRESHOLD: float = 0
    RERANK_BATCH_SIZE: int = 128


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
    _validate_data_repo_id = field_validator("DATA_REPO_ID", mode="after")(
        _require_non_empty_endpoint
    )


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
    _validate_tool_repo_id = field_validator("TOOL_REPO_ID", mode="after")(
        _require_non_empty_endpoint
    )
    OUTPUT_DIR: str = "/obs/phytomni/agent_data/test/output"
    COMPUTE_RESOURCE: Literal["small", "medium", "large"] = "small"
    TASK_NAME: str = "analyst-agents-task"
    RESOURCE: Dict[str, Dict[str, int]] = {
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
        Dict[str, str],
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
    DOWNLOAD_MARKER: Optional[str] = None
    DOWNLOAD_MAX_KEYS: int = 1000
    IF_DOWNLOAD_ALL: bool = True
    TARGET_FILE_FEATURE: List[str] = [""]
    PRE_PREPARED_DATA_PATH: str = str(PRE_PREPARED_DATA_PATH)
    POLL_INTERVAL: float = 300
    MAX_POLL: float = 86400
    PLAN_MIN_SCORE: int = 0

    # Dispatcher-side opt-in: when True, ``DigitalDesignAgents``
    # (and the upcoming Network / Research / Environment / DeepGenome
    # dispatchers) route their per-task analyst submission through
    # the analyst's compiled subgraph entry point
    # (``analyst_agent.app.ainvoke`` with ``AnalystInput``) instead of
    # the legacy ``analyst_agent.arun`` direct call. Default ``False``
    # keeps the production behavior identical to today; flip to
    # ``True`` per deployment once parent-graph composition has been
    # validated end-to-end. Inherited by every dispatcher config that
    # subclasses ``AnalystConfig``. ``AliasChoices`` mirrors the
    # endpoint-field pattern so ``PHYTOMNI_USE_ANALYST_SUBGRAPH``
    # routes to the same field as the unprefixed form.
    USE_ANALYST_SUBGRAPH: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices(
                "USE_ANALYST_SUBGRAPH", "PHYTOMNI_USE_ANALYST_SUBGRAPH"
            ),
        ),
    ] = False


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

    This agent combines direct BI database annotation lookup with literature
    retrieval, so it inherits the knowledge retrieval and chat defaults while
    adding the BI API endpoint.

    Attributes:
        BI_URL: BI API endpoint used for direct gene annotation lookup.
        TOP_N: Maximum number of literature retrieval results to keep.
    """

    BI_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("BI_URL", "PHYTOMNI_BI_URL"),
        ),
    ] = ""
    TOP_N: int = int(_MAX_TOKENS / 2048)

    # Public BI host required as an env var so every customer image
    # gets the endpoint from env, not from a hardcoded ``phytomni.cn``
    # default that would leak across deployments.
    _validate_bi_url = field_validator("BI_URL", mode="after")(
        _require_non_empty_endpoint
    )


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
    BI_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices("BI_URL", "PHYTOMNI_BI_URL"),
        ),
    ] = ""
    CREATE_TASK_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "CREATE_TASK_URL", "PHYTOMNI_CREATE_TASK_URL"
            ),
        ),
    ] = ""
    UPDATE_TASK_URL: Annotated[
        str,
        Field(
            default="",
            validation_alias=AliasChoices(
                "UPDATE_TASK_URL", "PHYTOMNI_UPDATE_TASK_URL"
            ),
        ),
    ] = ""
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
        "CREATE_TASK_URL",
        "UPDATE_TASK_URL",
        "SPA_FAQ_URL",
        "PROTOCOL_REPO_ID",
        "SPA_REPO_ID",
        "BI_URL",
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


_API_CACHE_DIR = Path(".cache") / "phytomni"


class ApiConfig(BaseSettings):
    """Non-secret configuration for the external HTTP API service.

    The SQLite store paths stay local-only on purpose: network
    filesystems deadlock under SQLite WAL. Each path accepts both a bare
    name and a ``PHYTOMNI_``-prefixed environment alias.

    Attributes:
        API_HOST (str): Bind host for the uvicorn server.
        API_PORT (int): Bind port for the uvicorn server.
        API_KEYS_DB_PATH (str): Local SQLite path for the API key store.
        API_TASKS_DB_PATH (str): Shared local SQLite path for the run
            registry and backend task status (single source of truth).
        API_REQUEST_TIMEOUT (float): Per-request timeout in seconds.
        API_RATE_LIMIT_PER_MIN (int): Per-key request budget per minute.
        API_RUN_TTL_OK_HOURS (int): Retention for terminal successful runs.
        API_RUN_TTL_FAIL_DAYS (int): Retention for failed or cancelled runs.
        API_SERVICE_TOKEN (Optional[SecretStr]): Privileged service-to-service
            credential read from the ``API_SERVICE_TOKEN`` environment
            variable or its ``PHYTOMNI_API_SERVICE_TOKEN`` alias (both
            names are accepted so docs, runbooks, and e2e helpers using
            either spelling keep working). When set, it authorises the
            upstream Web Go service (or any operator caller) to mint,
            list, and revoke per-user ``ptm_...`` keys via
            ``POST/GET/DELETE /v1/api-keys``. Treat as a root credential:
            the holder can issue keys for any ``user_id``. When unset,
            the ``/v1/api-keys/*`` routes return 503 so a development
            deployment that forgets to configure ops never silently
            exposes the path. The token is intentionally separate from
            ``ApiKeyStore`` so a leaked or compromised per-user key
            cannot escalate to issuance scope.
        API_UPLOAD_MAX_BYTES (int): Per-file size ceiling for
            ``POST /v1/files`` multipart uploads, in bytes. Defaults to
            25 MiB so a single agent-context attachment cannot exhaust the
            uvicorn worker's memory; oversize uploads return 413.
        API_UPLOAD_PREFIX (str): OBS object-key prefix below the bucket
            root for ``POST /v1/files`` outputs. Defaults to
            ``agent_data/uploads`` so user-supplied attachments live in a
            distinct namespace from per-run scratch
            (``agent_data/user_data/...``) and never collide with task
            output directories.
        RELAY_ENABLED (bool): When True, the server exposes the
            credential-injecting relay surface and writes relay audit
            records. Defaults to False so a stock deployment ships no
            relay. Accepts the ``PHYTOMNI_RELAY_ENABLED`` alias.
        RELAY_AUDIT_DB_PATH (str): Local SQLite path for the relay audit
            store. Kept local like the other stores because SQLite WAL
            deadlocks on network filesystems.
        RELAY_AUDIT_RETENTION_DAYS (int): Age in days after which relay
            audit records become eligible for retention cleanup.
        RELAY_REQUEST_MAX_BYTES (int): Maximum relayed request body size
            in bytes accepted before the relay rejects the call.
        RELAY_TIMEOUT_SECONDS (float): Per-request upstream timeout in
            seconds for relayed calls. Also reused as the wall-clock
            ceiling for a streamed forward so a drip-feeding upstream
            cannot hold a pooled connection indefinitely.
        RELAY_RESPONSE_AUDIT_MAX_BYTES (int): Maximum bytes of an upstream
            response copied into the audit store. The client-facing
            response is never truncated; only the audit copy is capped.
        RELAY_RATE_LIMIT_PER_MIN (int): Per-key request budget per minute
            for relay routes, kept distinct from API_RATE_LIMIT_PER_MIN
            because relay calls spend the operator's metered upstream
            credentials.
        RELAY_MAX_CONCURRENT_PER_KEY (int): Maximum in-flight relay
            forwards per key, bounding how many shared-pool connections a
            single tenant can hold open at once.
    """

    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8080
    API_KEYS_DB_PATH: str = Field(
        default=str(_API_CACHE_DIR / "api_keys.sqlite"),
        validation_alias=AliasChoices(
            "API_KEYS_DB_PATH", "PHYTOMNI_API_KEYS_DB"
        ),
    )
    API_TASKS_DB_PATH: str = Field(
        default="server_tasks.db",
        validation_alias=AliasChoices(
            "API_TASKS_DB_PATH", "PHYTOMNI_TASKS_DB"
        ),
    )
    API_REQUEST_TIMEOUT: float = 600.0
    API_RATE_LIMIT_PER_MIN: int = 120
    API_RUN_TTL_OK_HOURS: int = 24
    API_RUN_TTL_FAIL_DAYS: int = 7
    API_SERVICE_TOKEN: Annotated[
        Optional[SecretStr],
        Field(
            validation_alias=AliasChoices(
                "API_SERVICE_TOKEN", "PHYTOMNI_API_SERVICE_TOKEN"
            ),
        ),
    ] = None
    API_UPLOAD_MAX_BYTES: int = 26_214_400
    API_UPLOAD_PREFIX: str = "agent_data/uploads"
    RELAY_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "RELAY_ENABLED", "PHYTOMNI_RELAY_ENABLED"
        ),
    )
    RELAY_AUDIT_DB_PATH: str = Field(
        default=str(_API_CACHE_DIR / "relay_audit.sqlite"),
        validation_alias=AliasChoices(
            "RELAY_AUDIT_DB_PATH", "PHYTOMNI_RELAY_AUDIT_DB_PATH"
        ),
    )
    RELAY_AUDIT_RETENTION_DAYS: int = Field(
        default=90,
        validation_alias=AliasChoices(
            "RELAY_AUDIT_RETENTION_DAYS",
            "PHYTOMNI_RELAY_AUDIT_RETENTION_DAYS",
        ),
    )
    RELAY_REQUEST_MAX_BYTES: int = Field(
        default=10_485_760,
        validation_alias=AliasChoices(
            "RELAY_REQUEST_MAX_BYTES", "PHYTOMNI_RELAY_REQUEST_MAX_BYTES"
        ),
    )
    RELAY_TIMEOUT_SECONDS: float = Field(
        default=600.0,
        validation_alias=AliasChoices(
            "RELAY_TIMEOUT_SECONDS", "PHYTOMNI_RELAY_TIMEOUT_SECONDS"
        ),
    )
    RELAY_RESPONSE_AUDIT_MAX_BYTES: int = Field(
        default=10_485_760,
        validation_alias=AliasChoices(
            "RELAY_RESPONSE_AUDIT_MAX_BYTES",
            "PHYTOMNI_RELAY_RESPONSE_AUDIT_MAX_BYTES",
        ),
    )
    RELAY_RATE_LIMIT_PER_MIN: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "RELAY_RATE_LIMIT_PER_MIN", "PHYTOMNI_RELAY_RATE_LIMIT_PER_MIN"
        ),
    )
    RELAY_MAX_CONCURRENT_PER_KEY: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "RELAY_MAX_CONCURRENT_PER_KEY",
            "PHYTOMNI_RELAY_MAX_CONCURRENT_PER_KEY",
        ),
    )


SpeciesEntryValue = Union[str, Dict[str, str]]


class SpeciesDataIndex(
    RootModel[Dict[str, Dict[str, Dict[str, SpeciesEntryValue]]]]
):
    """Validation schema for species_data_list.json.

    The file follows the shape
    ``{analysis_type: {species_name: {key: leaf}}}`` where ``leaf`` is
    either an OBS file description string or a sub-dict mapping further
    keys (e.g. ``cultivars``, ``tissues``) to ``{file_path: description}``.
    """

    def analysis_types(self) -> List[str]:
        """Return the top-level analysis type keys.

        Returns:
            List of analysis-type keys present in the validated index.
        """
        return list(self.root.keys())

    def species_for(self, analysis_type: str) -> List[str]:
        """Return the species keys configured under ``analysis_type``.

        Args:
            analysis_type: Analysis type key (e.g. ``"evolution_analysis"``).

        Returns:
            List of species keys under the requested analysis type.
        """
        return list(self.root[analysis_type].keys())


class RegionMap(RootModel[Dict[str, Dict[str, Dict[str, str]]]]):
    """Validation schema for region_map.json.

    The file maps {province: {city: {district: region_code}}}; every leaf
    value is a pipe-delimited string like ``"310000|310000|310114"``.
    """

    def provinces(self) -> List[str]:
        """Return the top-level province keys.

        Returns:
            List of province keys present in the validated map.
        """
        return list(self.root.keys())

    def cities_for(self, province: str) -> List[str]:
        """Return the city keys configured under ``province``.

        Args:
            province: Province key (e.g. ``"北京市"``).

        Returns:
            List of city keys under the requested province.
        """
        return list(self.root[province].keys())


PromptLeaf = Union[str, Dict[str, str]]


class PromptTemplates(RootModel[Dict[str, Dict[str, PromptLeaf]]]):
    """Validation schema for .prompts.yaml.

    The file follows the shape ``{section: {key: leaf}}`` where
    ``section`` is one of ``system``, ``template``, or ``user``, and
    ``leaf`` is either a prompt template string (depth-2 entries) or a
    sub-dict of ``{name: template}`` entries (depth-3 entries used for
    nested prompt paths such as ``user/environment/get_code_query``).
    """

    def sections(self) -> List[str]:
        """Return the top-level prompt section keys.

        Returns:
            List of section keys present in the validated templates.
        """
        return list(self.root.keys())

    def keys_for(self, section: str) -> List[str]:
        """Return the prompt keys configured under ``section``.

        Args:
            section: Top-level section key (e.g. ``"system"``).

        Returns:
            List of prompt keys under the requested section.
        """
        return list(self.root[section].keys())
