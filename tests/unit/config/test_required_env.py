# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the side-effect-free deployable configuration contract."""

import re
from pathlib import Path

import pytest

from mcp_server_phytomni.config.required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
    REQUIRED_DEPLOYMENT_FIELDS,
    REQUIRED_OPERATOR_SECRET_FIELDS,
    REQUIRED_OUTBOUND_FIELDS,
    SERVER_REQUIRED_ENDPOINT_FIELDS,
    missing_environment,
)
from mcp_server_phytomni.config.settings import (
    _RELAY_OPTIONAL_SECRET_FIELDS,
)

pytestmark = pytest.mark.unit

_REPOSITORY_ROOT = Path(__file__).parents[3]

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


def test_outbound_required_fields_are_the_finite_logical_pool_contract() -> (
    None
):
    """The startup contract contains twelve capacities and one wait
    threshold."""
    expected = {
        "OUTBOUND_LLM_CONCURRENCY",
        "OUTBOUND_RETRIEVAL_CONCURRENCY",
        "OUTBOUND_RERANK_CONCURRENCY",
        "OUTBOUND_NL2SQL_CONCURRENCY",
        "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
        "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
        "OUTBOUND_IAM_CONCURRENCY",
        "OUTBOUND_SPA_FAQ_CONCURRENCY",
        "OUTBOUND_BI_CONCURRENCY",
        "OUTBOUND_OBS_CONCURRENCY",
        "OUTBOUND_RELAY_CONTROL_CONCURRENCY",
        "OUTBOUND_INTEROP_CONCURRENCY",
        "OUTBOUND_POOL_WAIT_WARN_SECONDS",
    }

    assert set(REQUIRED_OUTBOUND_FIELDS) == expected
    assert len(REQUIRED_OUTBOUND_FIELDS) == 13


def test_outbound_required_fields_are_documented_and_seeded_for_tests() -> (
    None
):
    """Every required pool setting appears once in docs, env, and test
    setup."""
    env_example = (
        _REPOSITORY_ROOT / "src/mcp_server_phytomni/config/.env.example"
    ).read_text(encoding="utf-8")
    documentation = (
        _REPOSITORY_ROOT / "docs/reference/configuration.md"
    ).read_text(encoding="utf-8")
    test_environment = (_REPOSITORY_ROOT / "conftest.py").read_text(
        encoding="utf-8"
    )

    for name in REQUIRED_OUTBOUND_FIELDS:
        assert (
            len(re.findall(rf"^{name}=", env_example, flags=re.MULTILINE)) == 1
        )
        assert documentation.count(f"`{name}`") == 1
        assert len(re.findall(rf'"{name}":', test_environment)) == 1
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
