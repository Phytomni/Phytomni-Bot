# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the side-effect-free deployable configuration contract."""

import re

import pytest

from mcp_server_phytomni.config.required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
    REQUIRED_DEPLOYMENT_FIELDS,
    REQUIRED_OPERATOR_SECRET_FIELDS,
    SERVER_REQUIRED_ENDPOINT_FIELDS,
    missing_environment,
)
from mcp_server_phytomni.config.settings import (
    _RELAY_OPTIONAL_SECRET_FIELDS,
)

pytestmark = pytest.mark.unit

EXPECTED_DEPLOYMENT_FIELDS = {
    *re.findall(
        r"[A-Z_]+",
        "TOKEN_URL RETRIEVE_URL RERANK_URL SPA_FAQ_URL DATABASE_URL "
        "ANALYSIS_URL OBS_SERVER REPO_ID REPO_ID_DICT WORKSPACE_ID SUBJECT_ID "
        "DATA_REPO_ID TOOL_REPO_ID PROTOCOL_REPO_ID SPA_REPO_ID APP_ID",
    ),
}


def test_deployment_required_fields_cover_every_runtime_subclass() -> None:
    """Every deployment field from the config hierarchy is represented."""
    assert set(REQUIRED_DEPLOYMENT_FIELDS) == EXPECTED_DEPLOYMENT_FIELDS
    assert len(REQUIRED_DEPLOYMENT_FIELDS) == 16
    assert set(DATA_REQUIRED_ENDPOINT_FIELDS) <= set(
        REQUIRED_DEPLOYMENT_FIELDS
    )
    assert set(ANALYST_REQUIRED_ENDPOINT_FIELDS) <= set(
        REQUIRED_DEPLOYMENT_FIELDS
    )
    assert set(DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS) <= set(
        REQUIRED_DEPLOYMENT_FIELDS
    )


def test_operator_secret_fields_match_required_sensitive_model_fields() -> (
    None
):
    """The relay optional list mirrors SensitiveConfig's required secrets."""
    assert len(REQUIRED_OPERATOR_SECRET_FIELDS) == 15
    assert "GAUSS_DSN" in REQUIRED_OPERATOR_SECRET_FIELDS
    assert "BI_TOKEN" not in REQUIRED_OPERATOR_SECRET_FIELDS
    assert set(REQUIRED_OPERATOR_SECRET_FIELDS) == set(
        _RELAY_OPTIONAL_SECRET_FIELDS
    )


def test_server_required_endpoint_fields_are_side_effect_free() -> None:
    """The base tuple remains the ten-field compatibility surface."""
    expected = tuple(
        f"{prefix}_{suffix}"
        for prefix, suffix in (
            ("TOKEN", "URL"),
            ("RETRIEVE", "URL"),
            ("RERANK", "URL"),
            ("DATABASE", "URL"),
            ("ANALYSIS", "URL"),
            ("REPO", "ID"),
            ("REPO", "ID_DICT"),
            ("WORKSPACE", "ID"),
            ("SUBJECT", "ID"),
            ("OBS", "SERVER"),
        )
    )
    assert expected == SERVER_REQUIRED_ENDPOINT_FIELDS


def test_missing_environment_is_sorted_and_value_safe() -> None:
    """Only missing names are returned, in deterministic order."""
    missing = missing_environment(
        ("TOKEN_URL", "GAUSS_DSN", "API_KEY"),
        {"TOKEN_URL": "https://example.invalid", "API_KEY": "secret"},
    )
    assert missing == ("GAUSS_DSN",)


def test_missing_environment_treats_blank_values_as_missing() -> None:
    """Whitespace-only environment values do not satisfy the contract."""
    assert missing_environment(
        ("B", "A", "C"), {"A": "  ", "B": "value", "C": ""}
    ) == ("A", "C")
