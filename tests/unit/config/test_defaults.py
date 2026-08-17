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

import mcp_server_phytomni.config as config_package
from mcp_server_phytomni.config import (
    AnalystConfig as PackageAnalystConfig,
)
from mcp_server_phytomni.config import (
    ApiConfig as PackageApiConfig,
)
from mcp_server_phytomni.config import (
    BriefGeneConfig as PackageBriefGeneConfig,
)
from mcp_server_phytomni.config import (
    ChatConfig as PackageChatConfig,
)
from mcp_server_phytomni.config import (
    CitationConfig as PackageCitationConfig,
)
from mcp_server_phytomni.config import (
    DataConfig as PackageDataConfig,
)
from mcp_server_phytomni.config import (
    DeepGenomeConfig as PackageDeepGenomeConfig,
)
from mcp_server_phytomni.config import (
    DigitalDesignConfig as PackageDigitalDesignConfig,
)
from mcp_server_phytomni.config import (
    EnvironmentConfig as PackageEnvironmentConfig,
)
from mcp_server_phytomni.config import (
    GeneNetworkConfig as PackageGeneNetworkConfig,
)
from mcp_server_phytomni.config import (
    InSilicoResearchConfig as PackageInSilicoResearchConfig,
)
from mcp_server_phytomni.config import (
    KnowledgeConfig as PackageKnowledgeConfig,
)
from mcp_server_phytomni.config import (
    PromptTemplates as PackagePromptTemplates,
)
from mcp_server_phytomni.config import (
    RegionMap as PackageRegionMap,
)
from mcp_server_phytomni.config import (
    ReviewConfig as PackageReviewConfig,
)
from mcp_server_phytomni.config import (
    ServerConfig as PackageServerConfig,
)
from mcp_server_phytomni.config import (
    SpeciesDataIndex as PackageSpeciesDataIndex,
)
from mcp_server_phytomni.config.defaults import (
    SERVER_REQUIRED_ENDPOINT_FIELDS,
    AnalystConfig,
    ApiConfig,
    BriefGeneConfig,
    ChatConfig,
    CitationConfig,
    DataConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    EnvironmentConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
    ServerConfig,
    resolve_compute_resource,
)
from mcp_server_phytomni.config.models.agents import (
    AnalystConfig as LeafAnalystConfig,
)
from mcp_server_phytomni.config.models.agents import (
    BriefGeneConfig as LeafBriefGeneConfig,
)
from mcp_server_phytomni.config.models.agents import (
    ChatConfig as LeafChatConfig,
)
from mcp_server_phytomni.config.models.agents import (
    DataConfig as LeafDataConfig,
)
from mcp_server_phytomni.config.models.agents import (
    DeepGenomeConfig as LeafDeepGenomeConfig,
)
from mcp_server_phytomni.config.models.agents import (
    DigitalDesignConfig as LeafDigitalDesignConfig,
)
from mcp_server_phytomni.config.models.agents import (
    EnvironmentConfig as LeafEnvironmentConfig,
)
from mcp_server_phytomni.config.models.agents import (
    GeneNetworkConfig as LeafGeneNetworkConfig,
)
from mcp_server_phytomni.config.models.agents import (
    InSilicoResearchConfig as LeafInSilicoResearchConfig,
)
from mcp_server_phytomni.config.models.agents import (
    KnowledgeConfig as LeafKnowledgeConfig,
)
from mcp_server_phytomni.config.models.agents import (
    ReviewConfig as LeafReviewConfig,
)
from mcp_server_phytomni.config.models.api import ApiConfig as LeafApiConfig
from mcp_server_phytomni.config.models.base import (
    ServerConfig as LeafServerConfig,
)
from mcp_server_phytomni.config.models.citation import (
    CitationConfig as LeafCitationConfig,
)
from mcp_server_phytomni.config.models.reference import (
    PromptTemplates as LeafPromptTemplates,
)
from mcp_server_phytomni.config.models.reference import (
    RegionMap as LeafRegionMap,
)
from mcp_server_phytomni.config.models.reference import (
    SpeciesDataIndex as LeafSpeciesDataIndex,
)
from mcp_server_phytomni.config.relay_mode import relay_mode_enabled
from mcp_server_phytomni.config.required_env import (
    REQUIRED_DEPLOYMENT_FIELDS,
    REQUIRED_OUTBOUND_FIELDS,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.unit


_CAPACITY_ENV_NAMES = REQUIRED_OUTBOUND_FIELDS[:-1]


_AGENT_MODEL_MANIFEST = (
    ("AnalystConfig", AnalystConfig, LeafAnalystConfig, PackageAnalystConfig),
    (
        "BriefGeneConfig",
        BriefGeneConfig,
        LeafBriefGeneConfig,
        PackageBriefGeneConfig,
    ),
    ("ChatConfig", ChatConfig, LeafChatConfig, PackageChatConfig),
    ("DataConfig", DataConfig, LeafDataConfig, PackageDataConfig),
    (
        "DeepGenomeConfig",
        DeepGenomeConfig,
        LeafDeepGenomeConfig,
        PackageDeepGenomeConfig,
    ),
    (
        "DigitalDesignConfig",
        DigitalDesignConfig,
        LeafDigitalDesignConfig,
        PackageDigitalDesignConfig,
    ),
    (
        "EnvironmentConfig",
        EnvironmentConfig,
        LeafEnvironmentConfig,
        PackageEnvironmentConfig,
    ),
    (
        "GeneNetworkConfig",
        GeneNetworkConfig,
        LeafGeneNetworkConfig,
        PackageGeneNetworkConfig,
    ),
    (
        "InSilicoResearchConfig",
        InSilicoResearchConfig,
        LeafInSilicoResearchConfig,
        PackageInSilicoResearchConfig,
    ),
    (
        "KnowledgeConfig",
        KnowledgeConfig,
        LeafKnowledgeConfig,
        PackageKnowledgeConfig,
    ),
    ("ReviewConfig", ReviewConfig, LeafReviewConfig, PackageReviewConfig),
)

_REFERENCE_MODEL_MANIFEST = (
    (
        "PromptTemplates",
        PackagePromptTemplates,
        LeafPromptTemplates,
    ),
    ("RegionMap", PackageRegionMap, LeafRegionMap),
    ("SpeciesDataIndex", PackageSpeciesDataIndex, LeafSpeciesDataIndex),
)


def test_interop_config_defaults_disabled_and_keeps_json_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ApiConfig does not eagerly parse interop target JSON."""
    monkeypatch.setenv("PHYTOMNI_INTEROP_TARGETS", "{malformed")

    config = ApiConfig()

    assert config.INTEROP_TARGETS.get_secret_value() == "{malformed"
    assert "{malformed" not in repr(config)
    assert "{malformed" not in str(config.model_dump())


def test_memory_config_defaults_disabled_and_supports_prefixed_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory stays dark by default and accepts the PHYTOMNI aliases."""
    monkeypatch.delenv("MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("PHYTOMNI_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTOMNI_MEMORY_DB_PATH", raising=False)

    default = ApiConfig()
    assert default.MEMORY_ENABLED is False
    assert str(default.MEMORY_DB_PATH).endswith(
        ".cache/phytomni/memory.sqlite"
    )

    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_MEMORY_DB_PATH", "/tmp/memory.sqlite")
    configured = ApiConfig()
    assert configured.MEMORY_ENABLED is True
    assert configured.MEMORY_DB_PATH == "/tmp/memory.sqlite"


def test_citation_config_is_lazy_and_supports_both_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Citation path settings remain optional until serving startup."""
    monkeypatch.delenv("CITATION_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTOMNI_CITATION_DB_PATH", raising=False)
    assert CitationConfig().CITATION_DB_PATH is None

    monkeypatch.setenv("PHYTOMNI_CITATION_DB_PATH", "/tmp/prefixed.sqlite")
    assert CitationConfig().CITATION_DB_PATH == "/tmp/prefixed.sqlite"

    monkeypatch.setenv("CITATION_DB_PATH", "/tmp/plain.sqlite")
    assert CitationConfig().CITATION_DB_PATH == "/tmp/plain.sqlite"


def test_citation_config_blank_path_normalizes_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace does not configure a citation database path."""
    monkeypatch.setenv("CITATION_DB_PATH", "   ")

    assert CitationConfig().CITATION_DB_PATH is None


def test_citation_config_blank_plain_alias_falls_back_to_prefixed_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank plain alias cannot mask a configured prefixed path."""
    monkeypatch.setenv("CITATION_DB_PATH", "   ")
    monkeypatch.setenv(
        "PHYTOMNI_CITATION_DB_PATH",
        "/tmp/prefixed.sqlite",
    )

    assert CitationConfig().CITATION_DB_PATH == "/tmp/prefixed.sqlite"


def test_citation_config_preserves_leaf_facade_package_identity() -> None:
    """Citation configuration retains the established export identity."""
    assert CitationConfig is LeafCitationConfig is PackageCitationConfig
    assert "CitationConfig" in config_package.__all__


def test_server_config_has_expected_core_defaults():
    """Verify server config has expected core defaults."""
    config = ServerConfig()

    assert config.MAX_TOKENS == 65536
    assert config.PROMPT_PATH == "system/ai4ps"
    assert config.PART_SIZE == 16777216
    assert Path(config.PROMPT_FILE).name == ".prompts.yaml"


def test_split_config_models_preserve_legacy_identity_and_shape() -> None:
    """Leaf models and legacy exports remain object- and shape-identical."""
    assert ServerConfig is LeafServerConfig is PackageServerConfig
    assert ApiConfig is LeafApiConfig is PackageApiConfig
    assert tuple(ServerConfig.model_fields) == tuple(
        LeafServerConfig.model_fields
    )
    assert tuple(ApiConfig.model_fields) == tuple(LeafApiConfig.model_fields)
    assert ServerConfig().model_dump() == LeafServerConfig().model_dump()
    assert ApiConfig().model_dump() == LeafApiConfig().model_dump()


def test_defaults_reexport_conversation_context_configuration() -> None:
    """Legacy defaults imports retain the conversation-context budgets."""
    config = ApiConfig()

    assert config.CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET == 6_000


@pytest.mark.parametrize(
    ("name", "legacy", "leaf", "package"),
    _AGENT_MODEL_MANIFEST,
)
def test_agent_model_manifest_preserves_legacy_exports(
    name,
    legacy,
    leaf,
    package,
) -> None:
    """Every agent model keeps identity, MRO, schema, and field defaults."""
    assert legacy is leaf is package
    assert legacy.__mro__ == leaf.__mro__
    assert tuple(legacy.model_fields) == tuple(leaf.model_fields)
    assert legacy.model_json_schema() == leaf.model_json_schema()
    assert legacy().model_dump() == leaf().model_dump()
    assert name in config_package.__all__


@pytest.mark.parametrize(
    ("name", "package", "leaf"), _REFERENCE_MODEL_MANIFEST
)
def test_reference_model_manifest_preserves_legacy_exports(
    name,
    package,
    leaf,
) -> None:
    """Reference models keep identity, schema, and package exports."""
    assert package is leaf
    assert package.__mro__ == leaf.__mro__
    assert package.model_json_schema() == leaf.model_json_schema()
    assert name in config_package.__all__


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


def test_analysis_timeout_budgets_are_independent() -> None:
    """Request, local polling, and remote job budgets stay independent."""
    config = AnalystConfig(
        TIMEOUT=11,
        MAX_POLL=22,
        ANALYSIS_JOB_TIMEOUT=33,
    )

    assert config.TIMEOUT == 11
    assert config.MAX_POLL == 22
    assert config.ANALYSIS_JOB_TIMEOUT == 33


def test_defaults_reexports_server_required_endpoint_fields() -> None:
    """Existing defaults imports keep the centralized tuple unchanged."""
    assert set(SERVER_REQUIRED_ENDPOINT_FIELDS) <= set(
        REQUIRED_DEPLOYMENT_FIELDS
    )


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


@pytest.mark.parametrize("name", _CAPACITY_ENV_NAMES)
def test_outbound_capacity_is_required(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject startup when one explicit outbound capacity is absent."""
    for env_name in _CAPACITY_ENV_NAMES:
        monkeypatch.setenv(env_name, "1")
    monkeypatch.setenv("OUTBOUND_POOL_WAIT_WARN_SECONDS", "0.1")
    monkeypatch.delenv(name)
    monkeypatch.delenv(f"PHYTOMNI_{name}", raising=False)

    with pytest.raises(ValidationError, match=name):
        ServerConfig()


@pytest.mark.parametrize("value", ["-1", "1.5", "NaN"])
def test_outbound_capacity_rejects_invalid_values(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept only integer capacities greater than or equal to zero."""
    monkeypatch.setenv("OUTBOUND_LLM_CONCURRENCY", value)

    with pytest.raises(ValidationError):
        ServerConfig()


@pytest.mark.parametrize(
    ("field", "env_name", "value"),
    [
        ("OUTBOUND_LLM_CONCURRENCY", "OUTBOUND_LLM_CONCURRENCY", "2"),
        (
            "OUTBOUND_RETRIEVAL_CONCURRENCY",
            "PHYTOMNI_OUTBOUND_RETRIEVAL_CONCURRENCY",
            "3",
        ),
        (
            "OUTBOUND_POOL_WAIT_WARN_SECONDS",
            "PHYTOMNI_OUTBOUND_POOL_WAIT_WARN_SECONDS",
            "0.2",
        ),
    ],
)
def test_outbound_settings_accept_both_alias_forms(
    field: str,
    env_name: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outbound configuration follows the unprefixed and prefixed contract."""
    monkeypatch.delenv(field, raising=False)
    monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)
    monkeypatch.setenv(env_name, value)

    expected = float(value) if "." in value else int(value)
    assert getattr(ServerConfig(), field) == expected


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "inf"])
def test_outbound_wait_warning_rejects_nonpositive_or_nonfinite_values(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait-warning threshold is finite and strictly positive."""
    monkeypatch.setenv("OUTBOUND_POOL_WAIT_WARN_SECONDS", value)

    with pytest.raises(ValidationError):
        ServerConfig()


def test_legacy_rerank_concurrency_cannot_satisfy_outbound_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The removed rerank setting cannot mask the required pool capacity."""
    monkeypatch.delenv("OUTBOUND_RERANK_CONCURRENCY", raising=False)
    monkeypatch.delenv("PHYTOMNI_OUTBOUND_RERANK_CONCURRENCY", raising=False)
    monkeypatch.setenv("RERANK_CONCURRENCY", "16")

    with pytest.raises(ValidationError, match="OUTBOUND_RERANK_CONCURRENCY"):
        ServerConfig()


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

    Pins the JSON-string env contract that ``docs/reference/configuration.md``
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


def test_compute_resource_table_and_agent_defaults() -> None:
    """Compute tiers live on config; Research/Environment override the default."""
    assert AnalystConfig.model_fields["RESOURCE"].default == {
        "small": {"cpu": 1, "memory": 4},
        "medium": {"cpu": 4, "memory": 16},
        "large": {"cpu": 16, "memory": 48},
    }
    assert AnalystConfig.model_fields["COMPUTE_RESOURCE"].default == "small"
    assert (
        InSilicoResearchConfig.model_fields["COMPUTE_RESOURCE"].default
        == "medium"
    )
    assert (
        EnvironmentConfig.model_fields["COMPUTE_RESOURCE"].default == "large"
    )
    assert resolve_compute_resource(AnalystConfig) == "small"
    assert resolve_compute_resource(InSilicoResearchConfig) == "medium"
    assert resolve_compute_resource(EnvironmentConfig) == "large"
    assert (
        resolve_compute_resource(
            DigitalDesignConfig, "protein_design_analysis"
        )
        == "medium"
    )
    assert (
        resolve_compute_resource(
            DigitalDesignConfig, "protein_structure_analysis"
        )
        == "medium"
    )
    assert (
        resolve_compute_resource(DigitalDesignConfig, "promoter_analysis")
        == "small"
    )
    assert (
        resolve_compute_resource(DeepGenomeConfig, "evolution_analysis")
        == "medium"
    )
    assert (
        resolve_compute_resource(DeepGenomeConfig, "single_cell_analysis")
        == "small"
    )


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
