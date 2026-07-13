# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for outbound interoperability target models."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from mcp_server_phytomni.interop.models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)

pytestmark = pytest.mark.unit


def _common_target(target_id: str) -> dict[str, object]:
    """Return policy fields shared by every target fixture."""
    return {
        "id": target_id,
        "credential_ref": "peer-auth",
        "connect_timeout_seconds": 5.0,
        "total_timeout_seconds": 30.0,
        "idle_timeout_seconds": 10.0,
        "response_max_bytes": 1_048_576,
        "discovery_ttl_seconds": 120.0,
        "private_cidr_allowlist": ["10.20.0.0/16"],
    }


@pytest.mark.parametrize(
    ("payload", "model_type"),
    [
        (
            {
                **_common_target("mcp-http"),
                "kind": "mcp",
                "transport": "streamable_http",
                "url": "https://mcp.example.test/v1/mcp",
                "allowed_tools": ["search_genes", "fetch.record"],
            },
            MCPStreamableHttpTarget,
        ),
        (
            {
                **_common_target("mcp-stdio"),
                "kind": "mcp",
                "transport": "stdio",
                "command": "/opt/phytomni/bin/peer-mcp",
                "args": ["--transport", "stdio"],
                "env_keys": ["LANG", "PATH"],
                "allowed_tools": ["annotate_gene"],
            },
            MCPStdioTarget,
        ),
        (
            {
                **_common_target("a2a-peer"),
                "kind": "a2a",
                "transport": "a2a",
                "card_base_url": "https://agent.example.test",
                "allowed_interface_origins": ["https://agent.example.test"],
                "allowed_interface_bindings": ["JSONRPC"],
                "allowed_skills": ["gene_annotation"],
            },
            A2ATarget,
        ),
    ],
)
def test_target_union_discriminates_supported_transports(
    payload: dict[str, object],
    model_type: type[MCPStreamableHttpTarget | MCPStdioTarget | A2ATarget],
) -> None:
    """The transport discriminator selects the exact target model."""
    adapter: TypeAdapter[InteropTarget] = TypeAdapter(InteropTarget)
    target = adapter.validate_python(payload)

    assert isinstance(target, model_type)
    assert target.credential_ref == "peer-auth"
    assert str(target.private_cidr_allowlist[0]) == "10.20.0.0/16"


@pytest.mark.parametrize(
    "field",
    ["headers", "authorization", "token", "password", "api_key"],
)
def test_target_rejects_embedded_secret_fields(field: str) -> None:
    """Targets carry only a credential reference, never secret material."""
    payload = {
        **_common_target("mcp-http"),
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/v1/mcp",
        "allowed_tools": ["search_genes"],
        field: "must-not-be-stored",
    }

    with pytest.raises(ValidationError, match="embedded credential"):
        TypeAdapter(InteropTarget).validate_python(payload)


@pytest.mark.parametrize(
    "capability",
    ["", "contains spaces", "../escape", "tool/name", "x" * 129],
)
def test_target_rejects_invalid_capability_names(capability: str) -> None:
    """Capability allowlists accept stable remote identifiers only."""
    payload = {
        **_common_target("mcp-http"),
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/v1/mcp",
        "allowed_tools": [capability],
    }

    with pytest.raises(ValidationError):
        TypeAdapter(InteropTarget).validate_python(payload)


def test_stdio_target_requires_absolute_command() -> None:
    """A stdio target cannot select a binary through cwd or PATH lookup."""
    payload = {
        **_common_target("mcp-stdio"),
        "kind": "mcp",
        "transport": "stdio",
        "command": "peer-mcp",
        "allowed_tools": ["annotate_gene"],
    }

    with pytest.raises(ValidationError, match="absolute"):
        TypeAdapter(InteropTarget).validate_python(payload)


def test_stdio_target_accepts_only_minimal_environment_keys() -> None:
    """The stdio child environment cannot inherit arbitrary secret keys."""
    payload = {
        **_common_target("mcp-stdio"),
        "kind": "mcp",
        "transport": "stdio",
        "command": str(Path("/opt/phytomni/bin/peer-mcp")),
        "env_keys": ["PATH", "API_KEY"],
        "allowed_tools": ["annotate_gene"],
    }

    with pytest.raises(ValidationError, match="env_keys"):
        TypeAdapter(InteropTarget).validate_python(payload)


@pytest.mark.parametrize(
    "argument",
    [
        "--token=placeholder",
        "--auth=placeholder",
        "--access-token=placeholder",
        "--client-secret=placeholder",
        "--header-file=/run/peer-auth",
        "--password-file=/run/peer-auth",
        "--api-key-file=/run/peer-auth",
        "--apikey=placeholder",
        "--apiKey=placeholder",
        "--clientSecret=placeholder",
        "--authorizationHeader=placeholder",
        "--accessKey=placeholder",
        "opaque-token-value",
    ],
)
def test_stdio_target_rejects_secret_command_arguments(argument: str) -> None:
    """Fixed stdio args cannot become a second credential store."""
    payload = {
        **_common_target("mcp-stdio"),
        "kind": "mcp",
        "transport": "stdio",
        "command": "/opt/phytomni/bin/peer-mcp",
        "args": [argument],
        "allowed_tools": ["annotate_gene"],
    }

    with pytest.raises(ValidationError, match="embedded credentials"):
        TypeAdapter(InteropTarget).validate_python(payload)


def test_stdio_target_rejects_authorization_header_pair() -> None:
    """The common ``-H Authorization`` form cannot embed a bearer token."""
    payload = {
        **_common_target("mcp-stdio"),
        "kind": "mcp",
        "transport": "stdio",
        "command": "/opt/phytomni/bin/peer-mcp",
        "args": ["-H", "Authorization: Bearer must-not-be-stored"],
        "allowed_tools": ["annotate_gene"],
    }

    with pytest.raises(ValidationError, match="embedded credentials"):
        TypeAdapter(InteropTarget).validate_python(payload)


@pytest.mark.parametrize(
    "argument",
    [
        "--config-file=/etc/peer/config.json",
        "--log-level=info",
        "--timeout=30",
        "--transport=stdio",
    ],
)
def test_stdio_target_preserves_normal_command_arguments(
    argument: str,
) -> None:
    """Non-sensitive fixed stdio options remain valid."""
    payload = {
        **_common_target("mcp-stdio"),
        "kind": "mcp",
        "transport": "stdio",
        "command": "/opt/phytomni/bin/peer-mcp",
        "args": [argument],
        "allowed_tools": ["annotate_gene"],
    }

    adapter: TypeAdapter[InteropTarget] = TypeAdapter(InteropTarget)
    target = adapter.validate_python(payload)

    assert isinstance(target, MCPStdioTarget)
    assert target.args == (argument,)
    assert argument not in repr(target)


def test_target_rejects_url_userinfo_as_embedded_credential() -> None:
    """Credentials cannot be smuggled through a configured target URL."""
    payload = {
        **_common_target("mcp-http"),
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://user:password@mcp.example.test/v1/mcp",
        "allowed_tools": ["search_genes"],
    }

    with pytest.raises(ValidationError, match="userinfo"):
        TypeAdapter(InteropTarget).validate_python(payload)


@pytest.mark.parametrize(
    "url",
    [
        " https://mcp.example.test/v1/mcp",
        "https://mcp.example.test/v1/mcp\n",
        "https://mcp.example.test/v1/\x80mcp",
        "https://mcp.example.test:invalid/v1/mcp",
        "https://mcp.example.test:70000/v1/mcp",
    ],
)
def test_http_target_rejects_malformed_url_structure(url: str) -> None:
    """Basic URL parsing rejects whitespace, controls, and invalid ports."""
    payload = {
        **_common_target("mcp-http"),
        "kind": "mcp",
        "transport": "streamable_http",
        "url": url,
        "allowed_tools": ["search_genes"],
    }

    with pytest.raises(ValidationError):
        TypeAdapter(InteropTarget).validate_python(payload)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://MCP.EXAMPLE.TEST:443/v1/Mcp/",
            "https://mcp.example.test/v1/Mcp/",
        ),
        (
            "HTTP://[2001:0DB8:0:0:0:0:0:1]:80/mcp",
            "http://[2001:db8::1]/mcp",
        ),
        (
            "https://MCP.EXAMPLE.TEST:8443/v1/mcp",
            "https://mcp.example.test:8443/v1/mcp",
        ),
    ],
)
def test_http_target_canonicalizes_endpoint_without_losing_path(
    url: str, expected: str
) -> None:
    """Endpoint canonicalization preserves path and non-default port."""
    payload = {
        **_common_target("mcp-http"),
        "kind": "mcp",
        "transport": "streamable_http",
        "url": url,
        "allowed_tools": ["search_genes"],
    }

    adapter: TypeAdapter[InteropTarget] = TypeAdapter(InteropTarget)
    target = adapter.validate_python(payload)

    assert isinstance(target, MCPStreamableHttpTarget)
    assert target.url == expected


def test_a2a_target_rejects_duplicate_canonical_interface_origins() -> None:
    """Equivalent origin spellings cannot appear twice in policy."""
    payload = {
        **_common_target("a2a-peer"),
        "kind": "a2a",
        "transport": "a2a",
        "card_base_url": "https://agent.example.test/card/base",
        "allowed_interface_origins": [
            "HTTPS://AGENT.EXAMPLE.TEST:443/",
            "https://agent.example.test",
        ],
        "allowed_interface_bindings": ["JSONRPC"],
        "allowed_skills": ["gene_annotation"],
    }

    with pytest.raises(ValidationError, match="duplicates"):
        TypeAdapter(InteropTarget).validate_python(payload)
