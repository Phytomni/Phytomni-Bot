# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API settings model.

A2A and relay stay behind ``A2A_ENABLED`` / ``RELAY_ENABLED``. There
is no A2UI enable flag: A2UI surfaces are always on. Conversation
context V1 is also always on in this process and does not read
``MEMORY_ENABLED``.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from ...runtime.resumable_uploads import MAX_UPLOAD_BYTES, PART_SIZE_BYTES
from ..api_limits import ApiLimitsConfig

_API_CACHE_DIR = Path(".cache") / "phytomni"


class ApiConfig(ApiLimitsConfig):
    """Non-secret configuration for the external HTTP API service.

    ``MEMORY_ENABLED`` gates the explicit cross-session memory store
    only. Conversation-context V1, A2UI, and upload routes do not
    consult it. ``A2A_ENABLED`` and ``RELAY_ENABLED`` remain real
    feature flags and stay off unless an operator turns them on.
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
        description=(
            "Enable the explicit cross-session memory store. "
            "Conversation-context V1 is always on and ignores this flag."
        ),
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
    EXECUTION_EVENTS_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "EXECUTION_EVENTS_ENABLED",
            "PHYTOMNI_EXECUTION_EVENTS_ENABLED",
        ),
    )
    EXECUTION_V1_COMPAT_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "EXECUTION_V1_COMPAT_ENABLED",
            "PHYTOMNI_EXECUTION_V1_COMPAT_ENABLED",
        ),
    )
    EXECUTION_LOG_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "EXECUTION_LOG_ENABLED",
            "PHYTOMNI_EXECUTION_LOG_ENABLED",
        ),
    )
    API_SERVICE_TOKEN: Annotated[
        SecretStr | None,
        Field(
            validation_alias=AliasChoices(
                "API_SERVICE_TOKEN", "PHYTOMNI_API_SERVICE_TOKEN"
            ),
        ),
    ] = None
    API_UPLOAD_PREFIX: str = "agent_data/uploads"
    API_UPLOAD_V2_ORIGIN: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_ORIGIN", "PHYTOMNI_API_UPLOAD_V2_ORIGIN"
        ),
    )
    API_UPLOAD_V2_BUCKET: str = Field(
        default="phytomni",
        min_length=1,
        max_length=63,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_BUCKET", "PHYTOMNI_API_UPLOAD_V2_BUCKET"
        ),
    )
    API_UPLOAD_V2_MAX_BYTES: int = Field(
        default=MAX_UPLOAD_BYTES,
        ge=1,
        le=MAX_UPLOAD_BYTES,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_MAX_BYTES", "PHYTOMNI_API_UPLOAD_V2_MAX_BYTES"
        ),
    )
    API_UPLOAD_V2_PART_SIZE_BYTES: int = Field(
        default=PART_SIZE_BYTES,
        ge=1,
        le=PART_SIZE_BYTES,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_PART_SIZE_BYTES",
            "PHYTOMNI_API_UPLOAD_V2_PART_SIZE_BYTES",
        ),
    )
    API_UPLOAD_V2_MAX_PARALLEL_PARTS: int = Field(
        default=4,
        ge=1,
        le=4,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_MAX_PARALLEL_PARTS",
            "PHYTOMNI_API_UPLOAD_V2_MAX_PARALLEL_PARTS",
        ),
    )
    API_UPLOAD_V2_CAPABILITY_TTL_SECONDS: int = Field(
        default=900,
        ge=60,
        le=900,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_CAPABILITY_TTL_SECONDS",
            "PHYTOMNI_API_UPLOAD_V2_CAPABILITY_TTL_SECONDS",
        ),
    )
    API_UPLOAD_V2_SESSION_TTL_SECONDS: int = Field(
        default=7 * 24 * 60 * 60,
        ge=3600,
        le=7 * 24 * 60 * 60,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_SESSION_TTL_SECONDS",
            "PHYTOMNI_API_UPLOAD_V2_SESSION_TTL_SECONDS",
        ),
    )
    API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS: int = Field(
        default=180 * 60,
        ge=60,
        le=7 * 24 * 60 * 60,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS",
            "PHYTOMNI_API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS",
        ),
    )
    API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=300,
        ge=30,
        le=24 * 60 * 60,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS",
            "PHYTOMNI_API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS",
        ),
    )
    API_UPLOAD_V2_ALLOWED_ORIGINS: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "API_UPLOAD_V2_ALLOWED_ORIGINS",
            "PHYTOMNI_API_UPLOAD_V2_ALLOWED_ORIGINS",
        ),
    )
    STREAM_ANSWER_MAX_BYTES: int = Field(
        default=1_048_576,
        validation_alias=AliasChoices(
            "STREAM_ANSWER_MAX_BYTES",
            "PHYTOMNI_STREAM_ANSWER_MAX_BYTES",
        ),
    )
    CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET: int = Field(
        default=6_000,
        ge=512,
        le=16_000,
        validation_alias=AliasChoices(
            "CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET",
            "PHYTOMNI_CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET",
        ),
    )
    CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET: int = Field(
        default=4_000,
        ge=512,
        le=16_000,
        validation_alias=AliasChoices(
            "CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET",
            "PHYTOMNI_CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET",
        ),
    )
    CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET: int = Field(
        default=3_000,
        ge=512,
        le=16_000,
        validation_alias=AliasChoices(
            "CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET",
            "PHYTOMNI_CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET",
        ),
    )
    CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET: int = Field(
        default=6_000,
        ge=512,
        le=16_000,
        validation_alias=AliasChoices(
            "CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET",
            "PHYTOMNI_CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET",
        ),
    )
    CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET: int = Field(
        default=4_000,
        ge=512,
        le=16_000,
        validation_alias=AliasChoices(
            "CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET",
            "PHYTOMNI_CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET",
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

    @field_validator("API_UPLOAD_V2_ORIGIN")
    @classmethod
    def _normalize_upload_origin(cls, value: str) -> str:
        """Require an absolute origin without credentials or query data."""
        normalized = value.strip().rstrip("/")
        if not _is_safe_http_url(normalized):
            raise ValueError(
                "API_UPLOAD_V2_ORIGIN must be an absolute HTTP(S) URL "
                "without credentials, query, or fragment."
            )
        return normalized

    @field_validator("API_UPLOAD_V2_ALLOWED_ORIGINS")
    @classmethod
    def _validate_upload_origins(cls, values: list[str]) -> list[str]:
        """Reject wildcard CORS origins at the upload boundary."""
        normalized: list[str] = []
        for value in values:
            candidate = value.strip().rstrip("/")
            if candidate == "*":
                raise ValueError(
                    "API_UPLOAD_V2_ALLOWED_ORIGINS cannot contain '*'"
                )
            if not _is_safe_http_url(candidate):
                raise ValueError(
                    "API_UPLOAD_V2_ALLOWED_ORIGINS must contain absolute "
                    "HTTP(S) origins without credentials or query data."
                )
            normalized.append(candidate)
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


def _is_safe_http_url(value: str) -> bool:
    """Return whether a URL is safe for an origin-style configuration."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    return not parsed.query and not parsed.fragment


__all__ = ["ApiConfig"]
