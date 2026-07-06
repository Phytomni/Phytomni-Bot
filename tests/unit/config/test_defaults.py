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
from mcp_server_phytomni.config.relay_mode import relay_mode_enabled
from mcp_server_phytomni.config.settings import SensitiveConfig

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


@pytest.mark.parametrize(
    "field",
    [
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


def test_deep_genome_config_has_no_task_url_fields():
    """CREATE_TASK_URL / UPDATE_TASK_URL are removed from DeepGenomeConfig."""
    assert "CREATE_TASK_URL" not in dict(DeepGenomeConfig.model_fields)
    assert "UPDATE_TASK_URL" not in dict(DeepGenomeConfig.model_fields)


@pytest.mark.parametrize("env_name", ["RELAY_MODE", "PHYTOMNI_RELAY_MODE"])
def test_relay_mode_field_parses_both_aliases(env_name, monkeypatch):
    """Both ``RELAY_MODE`` and ``PHYTOMNI_RELAY_MODE`` set the flag.

    The customer relay-mode switch follows the same dual-alias contract
    as every other ``PHYTOMNI_*`` toggle so a child deployment can use
    whichever prefix dominates its environment.
    """
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.setenv(env_name, "1")
    # Relay mode requires a base URL; supply it so construction succeeds.
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")

    config = ServerConfig()

    assert config.RELAY_MODE is True


def test_relay_mode_defaults_false_outside_relay(monkeypatch):
    """``RELAY_MODE`` is False when neither alias is set."""
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)

    assert ServerConfig().RELAY_MODE is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("on", True), ("0", False), ("", False)],
)
def test_relay_mode_enabled_helper_reads_env(value, expected, monkeypatch):
    """``relay_mode_enabled`` reads the flag straight from ``os.environ``.

    The import-time validator fork cannot consult a constructed config
    (the config is what is being built), so it reads the env directly
    through this helper. Pins the truthy parsing it shares with the
    ``RELAY_MODE`` bool field.
    """
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    if value:
        monkeypatch.setenv("PHYTOMNI_RELAY_MODE", value)

    assert relay_mode_enabled() is expected


def test_relay_mode_skips_endpoint_enforcement(monkeypatch):
    """In relay mode the 19 operator endpoints are no longer required.

    A customer child Bot bootstraps with only ``PHYTOMNI_RELAY_*`` set;
    it has no operator endpoints/UUIDs. The shared
    ``_require_non_empty_endpoint`` validator must short-circuit so
    ``ServerConfig()`` (built at import time in many modules) does not
    raise ``ValidationError`` during a relay-mode boot.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    for field in SERVER_REQUIRED_ENDPOINT_FIELDS:
        monkeypatch.delenv(field, raising=False)
        monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)

    config = ServerConfig()

    assert config.RELAY_MODE is True


def test_relay_mode_off_still_enforces_endpoints(monkeypatch):
    """With relay mode unset, missing endpoints still fail fast.

    Pins that the validator fork is gated strictly on relay mode and
    does not weaken the normal-mode startup contract.
    """
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("TOKEN_URL", raising=False)
    monkeypatch.delenv("PHYTOMNI_TOKEN_URL", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        ServerConfig()

    assert "TOKEN_URL" in str(excinfo.value)


def test_relay_base_url_strips_trailing_slash(monkeypatch):
    """``RELAY_BASE_URL`` is normalized without a trailing slash.

    The relay client appends ``/v1/relay/...`` paths, so a stored
    trailing slash would produce a double slash in the upstream URL.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test/api/")
    for field in SERVER_REQUIRED_ENDPOINT_FIELDS:
        monkeypatch.delenv(field, raising=False)
        monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)

    assert ServerConfig().RELAY_BASE_URL == "https://relay.test/api"


def test_relay_mode_requires_base_url(monkeypatch):
    """Enabling relay mode without a base URL fails fast.

    A child Bot that sets ``RELAY_MODE=1`` but forgets
    ``RELAY_BASE_URL`` would otherwise forward every dependency to an
    empty URL; surface the misconfiguration at startup instead.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_BASE_URL", raising=False)
    for field in SERVER_REQUIRED_ENDPOINT_FIELDS:
        monkeypatch.delenv(field, raising=False)
        monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        ServerConfig()

    assert "RELAY_BASE_URL" in str(excinfo.value)


def test_brief_gene_and_deep_genome_share_repo_id_dict_source():
    """Brief gene + deep genome configs share REPO_ID_DICT source.

    Both configs inherit ``REPO_ID_DICT`` from the root ``ServerConfig``
    declaration with the ``REPO_ID_DICT`` / ``PHYTOMNI_REPO_ID_DICT``
    env alias chain; neither subclass overrides the field. This pins
    the invariant so a future divergence (e.g. either subclass adding
    its own ``REPO_ID_DICT`` field with a different default or alias)
    breaks loud rather than silently splitting brief_gene's
    KnowledgeAgent retrieval corpus from deep_genome's. The invariant
    underpins composition: a consumer agent mounting brief_gene as a
    subgraph inherits brief_gene's retrieval semantics only as long
    as both configs resolve the same repository UUID map.
    """
    # With the test env staging a fixed PHYTOMNI_REPO_ID_DICT, both
    # subclasses inherit the same value via the shared
    # validation_alias chain; without it, both default to the same
    # ``{}``. The actual divergence this test catches is a
    # subclass-level shadow override that would resolve to a
    # different dict regardless of the env.
    brief_repo = BriefGeneConfig().REPO_ID_DICT
    deep_repo = DeepGenomeConfig().REPO_ID_DICT
    server_repo = ServerConfig().REPO_ID_DICT
    assert brief_repo == deep_repo
    assert brief_repo == server_repo


def test_brief_gene_config_has_no_bi_url() -> None:
    """BI_URL is gone from BriefGeneConfig after the GaussDB cutover."""
    assert "BI_URL" not in dict(BriefGeneConfig.model_fields)


def test_deep_genome_config_has_no_bi_url() -> None:
    """BI_URL is gone from DeepGenomeConfig after the GaussDB cutover."""
    assert "BI_URL" not in dict(DeepGenomeConfig.model_fields)


def test_sensitive_config_has_no_bi_token() -> None:
    """BI_TOKEN is gone from SensitiveConfig after the GaussDB cutover."""
    assert "BI_TOKEN" not in dict(SensitiveConfig.model_fields)
