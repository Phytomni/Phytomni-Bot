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
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    RootModel,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings

from .api_limits import ApiLimitsConfig
from .relay_mode import relay_mode_enabled
from .required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
    SERVER_REQUIRED_ENDPOINT_FIELDS,
)


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
    RETRIABLE_CODES: list[int] = [429, 500, 502, 503, 504]
    MAX_RETRIES: int = 5
    MAX_CONCURRENCY: int = 4
    MAX_WORKERS: int = 4
    GAUSS_COMMAND_TIMEOUT: float = 30.0
    HTTP_MAX_CONNECTIONS: int = 100
    HTTP_MAX_KEEPALIVE: int = 50

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
        dict[str, int],
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
        str | None,
        Field(
            default=None,
            validation_alias=AliasChoices("CA_BUNDLE", "PHYTOMNI_CA_BUNDLE"),
        ),
    ] = None

    # ``GRAPH_LOADER_ENABLED`` — when ``True``, the declarative
    # ``graphs.loader.load_graph_manifest`` may be called to read a
    # JSON manifest into a structural graph view. Default ``False``
    # makes the loader a no-op pathway: the call raises so the surface
    # stays inert in production and in offline tests.
    # The ``AliasChoices`` pair mirrors the endpoint-field pattern so
    # ``PHYTOMNI_GRAPH_LOADER=true`` routes to the same field as the
    # unprefixed form. Lives on ``ServerConfig`` so every subclass
    # inherits the field; the loader itself imports ``ServerConfig``
    # directly to read this flag.
    GRAPH_LOADER_ENABLED: Annotated[
        bool,
        Field(
            default=False,
            validation_alias=AliasChoices(
                "GRAPH_LOADER_ENABLED",
                "PHYTOMNI_GRAPH_LOADER",
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
    # Operator-assigned tenant id for a relay child. The server-side OBS
    # relay confines object keys to this tenant's namespace, so the child
    # roots its OBS paths here (see storage/path_policy.resolve_user_id).
    RELAY_USER_ID: str = Field(
        default="",
        validation_alias=AliasChoices(
            "RELAY_USER_ID", "PHYTOMNI_RELAY_USER_ID"
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


_API_CACHE_DIR = Path(".cache") / "phytomni"


class ApiConfig(ApiLimitsConfig):
    """Non-secret configuration for the external HTTP API service.

    The SQLite store paths stay local-only on purpose: network
    filesystems deadlock under SQLite WAL. Each path accepts both a bare
    name and a ``PHYTOMNI_``-prefixed environment alias.

    Attributes:
        API_HOST (str): Bind host for the uvicorn server.
        API_PORT (int): Bind port for the uvicorn server.
        API_GRACEFUL_SHUTDOWN (int): Maximum seconds uvicorn waits for
            in-flight connections to drain during shutdown before forcing
            exit; kept shorter than systemd's TimeoutStopSec so SSE chat,
            relay tee, and OBS download streams get a bounded window.
        API_KEYS_DB_PATH (str): Local SQLite path for the API key store.
        API_TASKS_DB_PATH (str): Shared local SQLite path for the run
            registry and backend task status (single source of truth).
        MEMORY_ENABLED (bool): Opt-in switch for user-scoped memory CRUD
            routes. Defaults to False so the public surface stays dark.
        MEMORY_DB_PATH (str): Local SQLite path for memory records; opened
            only when ``MEMORY_ENABLED`` is true.
        MEMORY_MAX_ITEMS (int): Maximum records retained per user namespace.
            Defaults to 100 and is bounded to prevent an unsafe override.
        MEMORY_MAX_CONTENT_BYTES (int): Maximum UTF-8 bytes in one memory
            record. Defaults to 16 KiB, matching the domain hard ceiling.
        MEMORY_MAX_TOTAL_BYTES (int): Maximum policy-counted bytes per user
            namespace. Defaults to 1 MiB.
        MEMORY_MAX_RETRIEVAL (int): Maximum memories returned to one graph
            read. Defaults to 20 and cannot exceed ``MEMORY_MAX_ITEMS``.
        MEMORY_GRAPH_MAX_BYTES (int): Maximum UTF-8 bytes returned to a graph
            read. Defaults to 64 KiB.
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
        STREAM_ANSWER_MAX_BYTES (int): Soft UTF-8 byte cap for the
            answer string persisted on ChatAgent streamed run records.
            The SSE wire stream is never truncated; only the registry
            blob is capped. Defaults to 1 MiB. Accepts the
            ``PHYTOMNI_STREAM_ANSWER_MAX_BYTES`` alias.
        A2UI_ENABLED (bool): When True, agents may emit A2UI confirm
            surfaces on the HTTP chat path. Defaults to False so A2UI
            stays dark until operators opt in. Accepts the
            ``PHYTOMNI_A2UI_ENABLED`` alias.
        A2UI_TOOL_CALL (bool): Reserved flag for future A2UI tool-call
            emit on the chat path. Defaults to False and is unused in
            the default path. Accepts the ``PHYTOMNI_A2UI_TOOL_CALL``
            alias.
        A2A_ENABLED (bool): When True, expose the feature-flagged A2A v1
            JSON-RPC surface. Defaults to False.
        A2A_PUBLIC_BASE_URL (Optional[str]): Public URL prefix used in the
            well-known Agent Card. Required when A2A is enabled; accepts
            HTTP(S) URLs and strips trailing slashes.
        INTEROP_ENABLED (bool): Opt-in switch for outbound MCP/A2A target
            loading. Defaults to False so malformed interop JSON remains
            inert until an operator enables the feature.
        INTEROP_TARGETS (SecretStr): Operator-owned target-registry JSON.
            Kept opaque here so disabled mode never parses it and routine
            config repr/model dumps do not expose endpoints or commands.
        INTEROP_MAX_TARGETS (int): Maximum operator-configured MCP/A2A
            targets accepted by the registry. Defaults to 64.
        INTEROP_CACHE_MAX_ENTRIES (int): Maximum successful target discovery
            results retained in each in-process cache. Defaults to 256.
        A2A_MAX_HISTORY_MESSAGES (int): Maximum request messages projected
            by ``GetTask``. Defaults to 32; zero disables history projection.
        A2A_MAX_ARTIFACT_BYTES (int): Maximum UTF-8 bytes in one local A2A
            answer artifact. Defaults to 256 KiB.
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
        RELAY_REQUEST_AUDIT_MAX_BYTES (int): Maximum UTF-8 bytes retained in
            the sanitized request-body audit copy. This is separate from the
            client-facing request limit.
        RELAY_TIMEOUT_SECONDS (float): Per-request upstream timeout in
            seconds for relayed calls. Also reused as the wall-clock
            ceiling for a streamed forward so a drip-feeding upstream
            cannot hold a pooled connection indefinitely.
        RELAY_RESPONSE_AUDIT_MAX_BYTES (int): Maximum bytes of an upstream
            response copied into the audit store. The client-facing
            response is never truncated; only the audit copy is capped.
        RELAY_RESPONSE_MAX_BYTES (int): Maximum size of an OBS object the
            download relay will stream back before returning 413, so one
            oversized object cannot exhaust the operator process memory.
            Distinct from RELAY_REQUEST_MAX_BYTES (request body cap).
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
    API_GRACEFUL_SHUTDOWN: int = 30
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
    MEMORY_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "MEMORY_ENABLED", "PHYTOMNI_MEMORY_ENABLED"
        ),
    )
    MEMORY_DB_PATH: str = Field(
        default=str(_API_CACHE_DIR / "memory.sqlite"),
        validation_alias=AliasChoices(
            "MEMORY_DB_PATH", "PHYTOMNI_MEMORY_DB_PATH"
        ),
    )
    API_REQUEST_TIMEOUT: float = 600.0
    API_RATE_LIMIT_PER_MIN: int = 120
    API_RUN_TTL_OK_HOURS: int = 24
    API_RUN_TTL_FAIL_DAYS: int = 7
    API_SERVICE_TOKEN: Annotated[
        SecretStr | None,
        Field(
            validation_alias=AliasChoices(
                "API_SERVICE_TOKEN", "PHYTOMNI_API_SERVICE_TOKEN"
            ),
        ),
    ] = None
    API_UPLOAD_MAX_BYTES: int = 26_214_400
    API_UPLOAD_PREFIX: str = "agent_data/uploads"
    STREAM_ANSWER_MAX_BYTES: int = Field(
        default=1_048_576,
        validation_alias=AliasChoices(
            "STREAM_ANSWER_MAX_BYTES",
            "PHYTOMNI_STREAM_ANSWER_MAX_BYTES",
        ),
    )
    A2UI_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices("A2UI_ENABLED", "PHYTOMNI_A2UI_ENABLED"),
    )
    A2UI_TOOL_CALL: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "A2UI_TOOL_CALL", "PHYTOMNI_A2UI_TOOL_CALL"
        ),
    )
    A2A_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices("A2A_ENABLED", "PHYTOMNI_A2A_ENABLED"),
    )
    A2A_PUBLIC_BASE_URL: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "A2A_PUBLIC_BASE_URL", "PHYTOMNI_A2A_PUBLIC_BASE_URL"
        ),
    )
    INTEROP_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "INTEROP_ENABLED", "PHYTOMNI_INTEROP_ENABLED"
        ),
    )
    INTEROP_TARGETS: Annotated[
        SecretStr,
        Field(
            default=SecretStr("[]"),
            validation_alias=AliasChoices(
                "INTEROP_TARGETS", "PHYTOMNI_INTEROP_TARGETS"
            ),
        ),
    ] = SecretStr("[]")
    RELAY_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "RELAY_ENABLED", "PHYTOMNI_RELAY_ENABLED"
        ),
    )

    @field_validator("A2A_PUBLIC_BASE_URL", mode="after")
    @classmethod
    def _normalize_a2a_public_base_url(cls, value: str | None) -> str | None:
        """Validate and normalize the public A2A URL prefix."""
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        has_url_restriction = any(
            (
                parsed.username is not None,
                parsed.password is not None,
                bool(parsed.query),
                bool(parsed.fragment),
            )
        )
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or has_url_restriction
        ):
            raise ValueError(
                "A2A_PUBLIC_BASE_URL must be an absolute HTTP(S) URL "
                "without credentials, query, or fragment."
            )
        return normalized

    @model_validator(mode="after")
    def _require_a2a_public_base_url(self) -> "ApiConfig":
        """Fail at settings construction when enabled A2A lacks a URL."""
        if self.A2A_ENABLED and not self.A2A_PUBLIC_BASE_URL:
            raise ValueError(
                "A2A_PUBLIC_BASE_URL is required when A2A_ENABLED is true; "
                "set A2A_PUBLIC_BASE_URL or PHYTOMNI_A2A_PUBLIC_BASE_URL."
            )
        return self

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
    RELAY_REQUEST_AUDIT_MAX_BYTES: int = Field(
        default=65_536,
        validation_alias=AliasChoices(
            "RELAY_REQUEST_AUDIT_MAX_BYTES",
            "PHYTOMNI_RELAY_REQUEST_AUDIT_MAX_BYTES",
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
    RELAY_RESPONSE_MAX_BYTES: int = Field(
        default=1024 * 1024 * 1024,
        validation_alias=AliasChoices(
            "RELAY_RESPONSE_MAX_BYTES", "PHYTOMNI_RELAY_RESPONSE_MAX_BYTES"
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
