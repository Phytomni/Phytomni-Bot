# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Immutable configuration models for operator-owned interop targets.

Classes: MCPStreamableHttpTarget, MCPStdioTarget, A2ATarget.
Types: InteropTarget.
"""

from __future__ import annotations

import re
from ipaddress import IPv6Address
from pathlib import Path
from typing import Annotated, Any, Literal
from unicodedata import category
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyNetwork,
    StringConstraints,
    field_validator,
    model_validator,
)

TargetId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    ),
]
CapabilityName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]
CredentialRef = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]

_EMBEDDED_CREDENTIAL_FIELDS = frozenset(
    {
        "api_key",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "header",
        "headers",
        "password",
        "secret",
        "token",
    }
)
_SECRET_OPTION_SEGMENTS = frozenset(
    {
        "auth",
        "authorization",
        "credential",
        "credentials",
        "header",
        "headers",
        "password",
        "secret",
        "token",
    }
)
_COMPACT_CREDENTIAL_COMPOUNDS = frozenset(
    {
        "accesskey",
        "apikey",
        "authorizationheader",
        "clientsecret",
    }
)
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _embedded_credential_field(value: Any) -> str | None:
    """Return the first secret-shaped mapping key in nested input."""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _EMBEDDED_CREDENTIAL_FIELDS:
                return str(key)
            found = _embedded_credential_field(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _embedded_credential_field(nested)
            if found is not None:
                return found
    return None


def _http_url(value: str, *, label: str, origin_only: bool = False) -> str:
    """Validate a configured HTTP(S) endpoint without resolving it."""
    if any(
        character.isspace() or category(character) == "Cc"
        for character in value
    ):
        raise ValueError(f"{label} must not contain whitespace or controls")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} contains an invalid host or port") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain URL userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain query or fragment")
    if parsed.netloc.endswith(":"):
        raise ValueError(f"{label} contains an invalid port")
    if origin_only and parsed.path not in {"", "/"}:
        raise ValueError(f"{label} must contain an origin without a path")

    canonical_hostname = hostname.lower()
    if ":" in canonical_hostname:
        try:
            canonical_hostname = IPv6Address(canonical_hostname).compressed
        except ValueError as exc:
            raise ValueError(f"{label} contains an invalid IPv6 host") from exc
        canonical_hostname = f"[{canonical_hostname}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = canonical_hostname
    if port is not None and not default_port:
        netloc = f"{netloc}:{port}"
    canonical_origin = f"{scheme}://{netloc}"
    if origin_only:
        return canonical_origin
    return urlunsplit((scheme, netloc, parsed.path, "", ""))


def _arg_embeds_credential(argument: str) -> bool:
    """Detect obvious credential-shaped static CLI arguments.

    Stdio targets are trusted operator-owned configuration. This check is a
    defense-in-depth rejection of recognizable credential/header option and
    value shapes; it cannot prove that arbitrary opaque values are not secret.
    """
    if argument == "-H" or argument.startswith("-H"):
        return True
    expanded = _CAMEL_CASE_BOUNDARY.sub("-", argument)
    segments = [
        segment
        for segment in re.split(r"[^a-z0-9]+", expanded.lower())
        if segment
    ]
    if _SECRET_OPTION_SEGMENTS.intersection(segments):
        return True
    adjacent_compounds = {
        segments[index] + segments[index + 1]
        for index in range(len(segments) - 1)
    }
    return bool(
        _COMPACT_CREDENTIAL_COMPOUNDS.intersection(
            {*segments, *adjacent_compounds}
        )
    )


class _InteropTargetBase(BaseModel):
    """Policy fields shared by every interop target."""

    id: TargetId
    credential_ref: CredentialRef | None = None
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300.0)
    total_timeout_seconds: float = Field(default=60.0, gt=0, le=3600.0)
    idle_timeout_seconds: float = Field(default=30.0, gt=0, le=600.0)
    response_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=1024 * 1024 * 1024,
    )
    discovery_ttl_seconds: float = Field(default=300.0, gt=0, le=86_400.0)
    private_cidr_allowlist: tuple[IPvAnyNetwork, ...] = ()

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_embedded_credentials(cls, data: Any) -> Any:
        """Reject secret-shaped fields while allowing ``credential_ref``."""
        found = _embedded_credential_field(data)
        if found is not None:
            raise ValueError(
                "target contains embedded credential field " f"{found!r}"
            )
        return data


class MCPStreamableHttpTarget(_InteropTargetBase):
    """An MCP peer reached through one fixed streamable-HTTP URL."""

    kind: Literal["mcp"]
    transport: Literal["streamable_http"]
    url: str
    allow_insecure_http: bool = False
    allowed_tools: tuple[CapabilityName, ...] = Field(min_length=1)

    @field_validator("url", mode="after")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Validate URL structure without checking peer reachability."""
        return _http_url(value, label="url")


class MCPStdioTarget(_InteropTargetBase):
    """An MCP peer executed from trusted operator-owned static config.

    Command and args are never request-provided. Credential-shaped argument
    rejection is defense in depth, not semantic inspection of opaque values.
    """

    kind: Literal["mcp"]
    transport: Literal["stdio"]
    command: str
    args: tuple[str, ...] = Field(default=(), max_length=32, repr=False)
    env_keys: tuple[Literal["LANG", "LC_ALL", "PATH", "TMPDIR"], ...] = ()
    allowed_tools: tuple[CapabilityName, ...] = Field(min_length=1)

    @field_validator("command", mode="after")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        """Require an absolute path without testing filesystem existence."""
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("stdio command must be an absolute path")
        return value

    @field_validator("args", mode="after")
    @classmethod
    def _reject_secret_args(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Prevent credentials from being embedded in fixed command args."""
        if any(_arg_embeds_credential(item) for item in value):
            raise ValueError(
                "stdio args must not contain embedded credentials"
            )
        return value


class A2ATarget(_InteropTargetBase):
    """An A2A peer constrained to operator-approved interfaces and skills."""

    kind: Literal["a2a"]
    transport: Literal["a2a"]
    card_base_url: str
    allow_insecure_http: bool = False
    allowed_interface_origins: tuple[str, ...] = Field(min_length=1)
    allowed_interface_bindings: tuple[
        Literal["JSONRPC", "HTTP+JSON", "GRPC"], ...
    ] = Field(default=("JSONRPC",), min_length=1)
    allowed_skills: tuple[CapabilityName, ...] = Field(min_length=1)

    @field_validator("card_base_url", mode="after")
    @classmethod
    def _validate_card_base_url(cls, value: str) -> str:
        """Validate the fixed Agent Card base without connecting to it."""
        return _http_url(value, label="card_base_url")

    @field_validator("allowed_interface_origins", mode="after")
    @classmethod
    def _validate_interface_origins(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Require explicit HTTP(S) origins rather than arbitrary URLs."""
        normalized = tuple(
            _http_url(item, label="interface origin", origin_only=True)
            for item in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("interface origins must not contain duplicates")
        return normalized


InteropTarget = Annotated[
    MCPStreamableHttpTarget | MCPStdioTarget | A2ATarget,
    Field(discriminator="transport"),
]

__all__ = [
    "A2ATarget",
    "InteropTarget",
    "MCPStdioTarget",
    "MCPStreamableHttpTarget",
]
