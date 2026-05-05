# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for config override helpers."""

from typing import Optional

from pydantic import BaseModel, SecretStr

from mcp_server_phytomni.config.overrides import (
    collect_mapped_overrides,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)


class DemoConfig(BaseModel):
    """Tiny config model used to test Pydantic model_copy behavior."""

    NAME: str = "base"
    COUNT: int = 1
    FLAG: bool = True
    OPTIONAL_VALUE: Optional[str] = "fallback"


class DemoSensitiveConfig(BaseModel):
    """Tiny sensitive config model used to test SecretStr handling."""

    BASE_URL: str = "https://base.example"
    MODEL_ID: str = "base-model"
    API_KEY: SecretStr = SecretStr("base-secret")


def test_collect_mapped_overrides_skips_none_but_keeps_falsy_values():
    updates = collect_mapped_overrides(
        {
            "name": "",
            "count": 0,
            "flag": False,
            "optional": None,
        },
        {
            "name": "NAME",
            "count": "COUNT",
            "flag": "FLAG",
            "optional": "OPTIONAL_VALUE",
        },
    )

    assert updates == {"NAME": "", "COUNT": 0, "FLAG": False}


def test_copy_config_with_overrides_keeps_base_for_none_values():
    config = copy_config_with_overrides(
        DemoConfig(),
        {"name": "custom", "optional": None},
        {"name": "NAME", "optional": "OPTIONAL_VALUE"},
        fixed_updates={"COUNT": 3},
    )

    assert config.NAME == "custom"
    assert config.COUNT == 3
    assert config.OPTIONAL_VALUE == "fallback"


def test_copy_sensitive_config_keeps_secret_fields_separate():
    config = copy_sensitive_config_with_overrides(
        DemoSensitiveConfig(),
        {
            "base_url": "https://custom.example",
            "model": "custom-model",
            "api_key": "custom-secret",
        },
        field_map={"base_url": "BASE_URL", "model": "MODEL_ID"},
        secret_field_map={"api_key": "API_KEY"},
    )

    assert config.BASE_URL == "https://custom.example"
    assert config.MODEL_ID == "custom-model"
    assert config.API_KEY.get_secret_value() == "custom-secret"


def test_copy_sensitive_config_accepts_existing_secretstr():
    secret = SecretStr("existing-secret")
    config = copy_sensitive_config_with_overrides(
        DemoSensitiveConfig(),
        {"api_key": secret},
        secret_field_map={"api_key": "API_KEY"},
    )

    assert config.API_KEY is secret
