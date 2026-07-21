# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Base settings model and deployment endpoint validation."""

from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings

from ..relay_mode import relay_mode_enabled
from ..required_env import SERVER_REQUIRED_ENDPOINT_FIELDS

_MAX_TOKENS = 65536
PARENT_PATH = Path(__file__).parent.parent.parent
PROMPT_PATH = PARENT_PATH / "config/.prompts.yaml"
PRE_PREPARED_DATA_PATH = PARENT_PATH / "config/species_data_list.json"
PRE_PREPARED_REGION_PATH = PARENT_PATH / "config/region_map.json"
DOWNLOAD_PATH = PARENT_PATH / ".out"
TEMP_PATH = PARENT_PATH / ".temp"


def _require_non_empty_endpoint(value, info: ValidationInfo):
    """Reject an empty deployment field with an env-name-aware message."""
    if relay_mode_enabled():
        return value
    if not value:
        raise ValueError(
            f"{info.field_name} is required; set the {info.field_name} "
            f"or PHYTOMNI_{info.field_name} environment variable."
        )
    return value


class ServerConfig(BaseSettings):
    """Configuration settings for server-level parameters and endpoints."""

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
    # ``default={}`` keeps the pydantic-settings field default visible to
    # type checkers; Pydantic v2 deep-copies dict literals per instance.
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
    RELAY_USER_ID: str = Field(
        default="",
        validation_alias=AliasChoices(
            "RELAY_USER_ID", "PHYTOMNI_RELAY_USER_ID"
        ),
    )

    _validate_server_endpoints = field_validator(
        *SERVER_REQUIRED_ENDPOINT_FIELDS,
        mode="after",
    )(_require_non_empty_endpoint)

    @field_validator("RELAY_BASE_URL", mode="after")
    @classmethod
    def _normalize_relay_base_url(cls, value: str) -> str:
        """Strip a trailing slash and require the URL in relay mode."""
        normalized = value.rstrip("/")
        if relay_mode_enabled() and not normalized:
            raise ValueError(
                "RELAY_BASE_URL is required in relay mode; set the "
                "RELAY_BASE_URL or PHYTOMNI_RELAY_BASE_URL environment "
                "variable."
            )
        return normalized


__all__ = [
    "DOWNLOAD_PATH",
    "PARENT_PATH",
    "PRE_PREPARED_DATA_PATH",
    "PRE_PREPARED_REGION_PATH",
    "PROMPT_PATH",
    "ServerConfig",
    "TEMP_PATH",
    "_MAX_TOKENS",
    "_require_non_empty_endpoint",
]
