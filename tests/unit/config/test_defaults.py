# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for non-secret default configuration models.

Covers the happy-path defaults, the env-required ``ValidationError``
contract that ``_require_non_empty_endpoint`` enforces on every
externalised endpoint / UUID / regional host, the ``PHYTOMNI_*``
alias path, and the JSON-string parsing of the two ``Dict``-valued
fields (``REPO_ID_DICT`` and ``APP_ID``).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config.defaults import (
    SERVER_REQUIRED_ENDPOINT_FIELDS,
    AnalystConfig,
    BriefGeneConfig,
    ChatConfig,
    DataConfig,
    DeepGenomeConfig,
    KnowledgeConfig,
    ServerConfig,
)

pytestmark = pytest.mark.unit


def test_server_config_has_expected_core_defaults():
    """Verify server config has expected core defaults."""
    config = ServerConfig()

    assert config.MAX_TOKENS == 65536
    assert config.PROMPT_PATH == "system/ai4ps"
    assert config.PART_SIZE == 16777216
    assert Path(config.PROMPT_FILE).name == ".prompts.yaml"


def test_config_inheritance_keeps_agent_defaults_available():
    """Verify config inheritance keeps agent defaults available."""
    chat_config = ChatConfig()
    knowledge_config = KnowledgeConfig()
    data_config = DataConfig()

    assert chat_config.RESPONSE_FORMAT == {"type": "json_object"}
    assert knowledge_config.PAGE_NUM == 1
    assert knowledge_config.SCOPE == "both"
    assert data_config.DATA_PAGE_SIZE == 3
    assert data_config.SIMPLIFY_RESPONSE is True


@pytest.mark.parametrize("field", SERVER_REQUIRED_ENDPOINT_FIELDS)
def test_server_config_missing_required_env_raises(field, monkeypatch):
    """Each ServerConfig env-required field rejects empty values.

    Removing both alias forms (the unprefixed and ``PHYTOMNI_*``
    variants) drops the value to the empty default, and the shared
    ``_require_non_empty_endpoint`` validator must raise
    ``ValidationError`` naming the field so an operator sees the
    env-var label they need to set.
    """
    monkeypatch.delenv(field, raising=False)
    monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        ServerConfig()

    assert field in str(excinfo.value)


def test_server_config_accepts_phytomni_prefixed_alias(monkeypatch):
    """The ``PHYTOMNI_<NAME>`` alias resolves the same field.

    Pins the ``AliasChoices`` contract so a deployment can use the
    prefixed form when other ``PHYTOMNI_*`` variables already
    dominate the runtime environment.
    """
    monkeypatch.delenv("TOKEN_URL", raising=False)
    monkeypatch.setenv(
        "PHYTOMNI_TOKEN_URL", "https://example.invalid/iam-alias"
    )

    config = ServerConfig()

    assert config.TOKEN_URL == "https://example.invalid/iam-alias"


def test_data_config_missing_data_repo_id_raises(monkeypatch):
    """``DataConfig.DATA_REPO_ID`` is env-required on top of base fields."""
    monkeypatch.delenv("DATA_REPO_ID", raising=False)
    monkeypatch.delenv("PHYTOMNI_DATA_REPO_ID", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        DataConfig()

    assert "DATA_REPO_ID" in str(excinfo.value)


def test_analyst_config_missing_tool_repo_id_raises(monkeypatch):
    """``AnalystConfig.TOOL_REPO_ID`` is env-required on top of base fields."""
    monkeypatch.delenv("TOOL_REPO_ID", raising=False)
    monkeypatch.delenv("PHYTOMNI_TOOL_REPO_ID", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        AnalystConfig()

    assert "TOOL_REPO_ID" in str(excinfo.value)


def test_analyst_config_missing_app_id_raises(monkeypatch):
    """AnalystConfig.APP_ID rejects an empty dict from missing env.

    The shared ``_require_non_empty_endpoint`` validator treats an
    empty ``Dict[str, str]`` the same as an empty string: both are
    falsy and must trip the deployment-misconfig signal at startup.
    """
    monkeypatch.delenv("APP_ID", raising=False)
    monkeypatch.delenv("PHYTOMNI_APP_ID", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        AnalystConfig()

    assert "APP_ID" in str(excinfo.value)


def test_analyst_config_parses_app_id_json_env(monkeypatch):
    """Pydantic-settings parses the APP_ID JSON-string env into Dict[str, str].

    Pins the JSON-string env contract that ``docs/configuration.md``
    documents (parallel to the existing ``REPO_ID_DICT`` pattern):
    a single env var carries the full compute-tier map rather than
    needing three independent ``APP_ID_SMALL`` / ``..._MEDIUM`` / etc.
    """
    monkeypatch.setenv(
        "APP_ID",
        '{"small": "tier-s", "medium": "tier-m", "large": "tier-l"}',
    )

    config = AnalystConfig()

    assert config.APP_ID == {
        "small": "tier-s",
        "medium": "tier-m",
        "large": "tier-l",
    }


def test_brief_gene_config_missing_bi_url_raises(monkeypatch):
    """BriefGeneConfig.BI_URL is env-required (legacy default: phytomni.cn)."""
    monkeypatch.delenv("BI_URL", raising=False)
    monkeypatch.delenv("PHYTOMNI_BI_URL", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        BriefGeneConfig()

    assert "BI_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "field",
    [
        "CREATE_TASK_URL",
        "UPDATE_TASK_URL",
        "SPA_FAQ_URL",
        "PROTOCOL_REPO_ID",
        "SPA_REPO_ID",
    ],
)
def test_deep_genome_config_missing_required_env_raises(field, monkeypatch):
    """DeepGenome-specific endpoints / repo UUIDs are env-required."""
    monkeypatch.delenv(field, raising=False)
    monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        DeepGenomeConfig()

    assert field in str(excinfo.value)
