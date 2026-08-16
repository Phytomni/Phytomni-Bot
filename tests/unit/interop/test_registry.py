# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the interoperability target registry."""

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.interop.models import MCPStreamableHttpTarget
from mcp_server_phytomni.interop.registry import (
    InteropRegistry,
    InteropRegistryError,
    load_interop_registry,
)

pytestmark = pytest.mark.unit


def _target(target_id: str = "mcp-http") -> dict[str, object]:
    """Return a minimal valid streamable-HTTP target payload."""
    return {
        "id": target_id,
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/v1/mcp",
        "allowed_tools": ["search_genes"],
        "credential_ref": "peer-auth",
    }


def _api_config(*, targets: str, max_targets: int | None = None) -> ApiConfig:
    """Build an ApiConfig isolated from the developer dotenv file."""
    config_cls = cast(Any, ApiConfig)
    values: dict[str, object] = {
        "_env_file": None,
        "INTEROP_TARGETS": targets,
    }
    if max_targets is not None:
        values["INTEROP_MAX_TARGETS"] = max_targets
    return config_cls(
        **values,
    )


def test_enabled_registry_loads_targets_by_operator_owned_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers resolve an immutable operator target by id only."""
    monkeypatch.setenv(
        "PHYTOMNI_INTEROP_CREDENTIALS",
        json.dumps(
            {"peer-auth": {"headers": {"Authorization": "Bearer secret"}}}
        ),
    )
    config = _api_config(targets=json.dumps([_target()]))

    registry = load_interop_registry(config)

    target = registry.require_target("mcp-http")
    assert registry.target_ids() == ("mcp-http",)
    assert isinstance(target, MCPStreamableHttpTarget)
    assert not hasattr(registry, "credentials")
    assert "Bearer secret" not in repr(registry)


def test_enabled_registry_rejects_duplicate_target_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate ids cannot make target lookup order-dependent."""
    monkeypatch.setenv(
        "PHYTOMNI_INTEROP_CREDENTIALS",
        '{"peer-auth": {"headers": {"X-Peer-Key": "x"}}}',
    )
    config = _api_config(
        targets=json.dumps([_target(), _target()]),
    )

    with pytest.raises(InteropRegistryError, match="duplicate target id"):
        load_interop_registry(config)


def test_enabled_registry_rejects_target_count_over_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry rejects an oversized operator target list early."""
    monkeypatch.setenv(
        "PHYTOMNI_INTEROP_CREDENTIALS",
        '{"peer-auth": {"headers": {"X-Peer-Key": "x"}}}',
    )
    config = _api_config(
        targets=json.dumps([_target("mcp-http"), _target("mcp-http-2")]),
        max_targets=1,
    )

    with pytest.raises(InteropRegistryError, match="target limit"):
        load_interop_registry(config)


def test_enabled_registry_rejects_missing_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every non-empty credential reference must exist in secret JSON."""
    monkeypatch.setenv("PHYTOMNI_INTEROP_CREDENTIALS", "{}")
    config = _api_config(targets=json.dumps([_target()]))

    with pytest.raises(InteropRegistryError, match="peer-auth"):
        load_interop_registry(config)


def test_enabled_registry_suppresses_invalid_target_payload_from_error_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected embedded token cannot survive in an exception chain."""
    monkeypatch.setenv("PHYTOMNI_INTEROP_CREDENTIALS", "{}")
    target = _target()
    target["token"] = "must-not-be-stored"
    config = _api_config(targets=json.dumps([target]))

    with pytest.raises(InteropRegistryError) as excinfo:
        load_interop_registry(config)

    assert "must-not-be-stored" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


@pytest.mark.parametrize(
    ("targets", "credentials", "message"),
    [
        ("{bad", "{}", "INTEROP_TARGETS"),
        (json.dumps([_target()]), "[not-a-map]", "INTEROP_CREDENTIALS"),
        (json.dumps([_target()]), '{"peer-auth": "plain"}', "credential"),
    ],
)
def test_enabled_registry_fails_fast_on_malformed_configuration(
    targets: str,
    credentials: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed enabled configuration fails during registry loading."""
    monkeypatch.setenv("PHYTOMNI_INTEROP_CREDENTIALS", credentials)
    config = _api_config(targets=targets)

    with pytest.raises((InteropRegistryError, ValidationError), match=message):
        load_interop_registry(config)


def test_registry_missing_target_error_does_not_echo_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lookup failures contain only the requested target id."""
    monkeypatch.setenv(
        "PHYTOMNI_INTEROP_CREDENTIALS",
        '{"peer-auth": {"headers": {"X-Peer-Key": "x"}}}',
    )
    config = _api_config(targets=json.dumps([_target()]))
    registry = load_interop_registry(config)

    with pytest.raises(InteropRegistryError) as excinfo:
        registry.require_target("missing")

    assert "missing" in str(excinfo.value)
    assert "mcp.example.test" not in str(excinfo.value)


def test_registry_copies_and_freezes_supplied_target_mapping() -> None:
    """Caller mutation cannot alter a constructed registry."""
    target = MCPStreamableHttpTarget.model_validate(_target())
    supplied = {target.id: target}
    registry = InteropRegistry(_targets=supplied)

    supplied.clear()

    assert registry.target_ids() == ("mcp-http",)
    frozen_targets = getattr(registry, "_targets")
    with pytest.raises(TypeError):
        frozen_targets["other"] = target


def test_registry_rejects_mapping_key_that_differs_from_target_id() -> None:
    """Lookup keys cannot disagree with the immutable target identity."""
    target = MCPStreamableHttpTarget.model_validate(_target())

    with pytest.raises(InteropRegistryError, match="mapping key"):
        InteropRegistry(_targets={"other": target})
