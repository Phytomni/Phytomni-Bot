# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Optional citation database configuration."""

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class CitationConfig(BaseSettings):
    """Non-secret citation database path without startup-side effects."""

    CITATION_DB_PATH: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CITATION_DB_PATH", "PHYTOMNI_CITATION_DB_PATH"
        ),
    )

    @field_validator("CITATION_DB_PATH", mode="before")
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        """Keep absent and whitespace-only paths equally unconfigured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


__all__ = ["CitationConfig"]
