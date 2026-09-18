# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Optional citation database configuration."""

import os

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class CitationConfig(BaseSettings):
    """Non-secret citation database path without startup-side effects."""

    CITATION_DB_PATH: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CITATION_DB_PATH", "PHYTOMNI_CITATION_DB_PATH"
        ),
    )
    CITATION_OMIT_UNMATCHED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "CITATION_OMIT_UNMATCHED",
            "PHYTOMNI_CITATION_OMIT_UNMATCHED",
        ),
    )

    @field_validator("CITATION_DB_PATH", mode="before")
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        """Keep absent and whitespace-only paths equally unconfigured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _fallback_to_nonblank_prefixed_path(self) -> "CitationConfig":
        """Use the prefixed alias when a present plain alias is blank."""
        if self.CITATION_DB_PATH is not None:
            return self
        fallback = os.environ.get("PHYTOMNI_CITATION_DB_PATH")
        if fallback is not None and fallback.strip():
            object.__setattr__(self, "CITATION_DB_PATH", fallback)
        return self


__all__ = ["CitationConfig"]
