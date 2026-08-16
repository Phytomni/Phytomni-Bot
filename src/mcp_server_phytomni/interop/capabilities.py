# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Normalize discovered external tools into safe, non-executable DTOs.

The discovery boundary deliberately separates capability metadata from the
temporary LangChain tools returned by :mod:`interop.mcp_client`.  A capability
never retains a ``BaseTool``, client, transport, or credential-bearing
closure, so it is safe to expose to later discovery and routing layers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool

from .mcp_client import (
    InteropMCPError,
    load_external_mcp_tools,
)
from .models import InteropTarget
from .registry import InteropRegistry, InteropRegistryError

if TYPE_CHECKING:
    from .cache import DiscoveryCache

MAX_CAPABILITIES = 256
MAX_DESCRIPTION_BYTES = 4096
MAX_SCHEMA_BYTES = 64 * 1024
MAX_QUALIFIED_NAME_BYTES = 256

_CAPABILITY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TARGET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class InteropCapabilityError(ValueError):
    """Raised when untrusted discovery metadata cannot be normalized."""

    def __init__(self, code: str) -> None:
        super().__init__(f"external interop capability {code}")
        self.code = code


class _DiscoveryFailureError(Exception):
    """Internal marker for an unexpected discovery failure."""


@dataclass(frozen=True, slots=True)
class InteropCapability:
    """Immutable, non-executable metadata for one remote capability."""

    target_id: str
    kind: str
    remote_name: str
    qualified_name: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Validate and defensively copy the externally supplied metadata."""
        _validate_target_id(self.target_id)
        if self.kind not in {"mcp", "a2a"}:
            raise InteropCapabilityError("invalid_kind")
        _validate_capability_name(self.remote_name)
        expected = f"{self.target_id}__{self.remote_name}"
        if self.qualified_name != expected:
            raise InteropCapabilityError("invalid_qualified_name")
        if len(self.qualified_name.encode("utf-8")) > MAX_QUALIFIED_NAME_BYTES:
            raise InteropCapabilityError("qualified_name_too_large")
        if not isinstance(self.description, str):
            raise InteropCapabilityError("invalid_description")
        if len(self.description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
            raise InteropCapabilityError("description_too_large")
        schema = _canonical_schema(self.input_schema)
        object.__setattr__(self, "input_schema", schema)

    @property
    def json_schema(self) -> Mapping[str, Any]:
        """Compatibility alias for consumers that call the JSON schema."""
        return self.input_schema

    def model_dump(self) -> dict[str, Any]:
        """Return a detached JSON-compatible representation of the DTO."""
        return {
            "target_id": self.target_id,
            "kind": self.kind,
            "remote_name": self.remote_name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "input_schema": _thaw(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryError:
    """Sanitized target-level discovery error."""

    target_id: str
    kind: str
    code: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Deterministic capability data plus sanitized discovery failures."""

    data: tuple[InteropCapability, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()

    def __post_init__(self) -> None:
        """Freeze caller-provided iterables and preserve stable ordering."""
        data = tuple(self.data)
        errors = tuple(self.errors)
        if any(not isinstance(item, InteropCapability) for item in data):
            raise TypeError("discovery data must contain capabilities")
        if any(not isinstance(item, DiscoveryError) for item in errors):
            raise TypeError("discovery errors must contain DiscoveryError")
        object.__setattr__(
            self,
            "data",
            tuple(sorted(data, key=lambda item: item.qualified_name)),
        )
        object.__setattr__(
            self,
            "errors",
            tuple(
                sorted(
                    errors,
                    key=lambda item: (
                        item.target_id,
                        item.kind,
                        item.code,
                    ),
                )
            ),
        )


def _validate_target_id(target_id: str) -> None:
    if not isinstance(target_id, str) or not _TARGET_ID.fullmatch(target_id):
        raise InteropCapabilityError("invalid_target_id")


def _validate_capability_name(remote_name: str) -> None:
    if not isinstance(remote_name, str) or not _CAPABILITY_NAME.fullmatch(
        remote_name
    ):
        raise InteropCapabilityError("invalid_remote_name")


def _remote_name(raw_name: object, target_id: str) -> str:
    """Recover the unqualified name from either adapter separator spelling."""
    if not isinstance(raw_name, str) or not raw_name:
        raise InteropCapabilityError("missing_remote_name")
    for separator in ("__", "_"):
        prefix = f"{target_id}{separator}"
        if raw_name.startswith(prefix):
            remote_name = raw_name.removeprefix(prefix)
            if remote_name:
                return remote_name
            break
    return raw_name


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_thaw(nested) for nested in value]
    return value


def _canonical_schema(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InteropCapabilityError("invalid_schema")
    try:
        encoded = json.dumps(
            _thaw(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        detached = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise InteropCapabilityError("invalid_schema") from None
    if not isinstance(detached, dict):
        raise InteropCapabilityError("invalid_schema")
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise InteropCapabilityError("schema_too_large")
    return detached


def _tool_schema(tool: BaseTool) -> Mapping[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        candidate = args_schema.model_json_schema()
    elif args_schema is not None and hasattr(args_schema, "schema"):
        candidate = args_schema.schema()
    else:
        candidate = getattr(tool, "args", {})
    if not isinstance(candidate, Mapping):
        raise InteropCapabilityError("invalid_schema")
    return candidate


def normalize_capabilities(
    target: str | InteropTarget,
    tools: Iterable[BaseTool],
    *,
    kind: str | None = None,
) -> tuple[InteropCapability, ...]:
    """Convert temporary executable tools into deterministic DTOs.

    ``target`` may be a validated target model or a target id.  Accepting the
    model lets C3.5 reuse this normalizer while the id-only form keeps the
    public discovery seam independent from request-provided endpoints.
    """
    target_id = target if isinstance(target, str) else target.id
    resolved_kind = kind or (
        target.kind if not isinstance(target, str) else "mcp"
    )
    _validate_target_id(target_id)
    if resolved_kind not in {"mcp", "a2a"}:
        raise InteropCapabilityError("invalid_kind")

    capabilities: list[InteropCapability] = []
    seen: set[str] = set()
    for tool in tools:
        remote_name = _remote_name(getattr(tool, "name", None), target_id)
        _validate_capability_name(remote_name)
        qualified_name = f"{target_id}__{remote_name}"
        if qualified_name in seen:
            raise InteropCapabilityError("qualified_name_conflict")
        seen.add(qualified_name)
        description = getattr(tool, "description", "") or ""
        if not isinstance(description, str):
            raise InteropCapabilityError("invalid_description")
        capabilities.append(
            InteropCapability(
                target_id=target_id,
                kind=resolved_kind,
                remote_name=remote_name,
                qualified_name=qualified_name,
                description=description,
                input_schema=_tool_schema(tool),
            )
        )
        if len(capabilities) > MAX_CAPABILITIES:
            raise InteropCapabilityError("too_many_capabilities")

    return tuple(sorted(capabilities, key=lambda item: item.qualified_name))


def _target_kind(registry: InteropRegistry, target_id: str) -> str:
    try:
        return registry.require_target(target_id).kind
    except InteropRegistryError:
        return "mcp"


async def discover_external_mcp_capabilities(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: Any | None = None,
    resolver: Any = None,
    _client_cls: type[Any] | None = None,
    cache: DiscoveryCache | None = None,
) -> DiscoveryResult:
    """Discover one MCP target and return data/errors without peer details."""
    kind = _target_kind(registry, target_id)
    kwargs: dict[str, Any] = {"registry": registry}
    if sensitive_config is not None:
        kwargs["sensitive_config"] = sensitive_config
    if resolver is not None:
        kwargs["resolver"] = resolver
    if _client_cls is not None:
        kwargs["_client_cls"] = _client_cls

    async def _discover_once(_: str) -> DiscoveryResult:
        try:
            data = await _load_and_normalize(target_id, kind, kwargs)
        except InteropMCPError as exc:
            return DiscoveryResult(
                errors=(DiscoveryError(target_id, kind, exc.code),)
            )
        except InteropCapabilityError as exc:
            return DiscoveryResult(
                errors=(DiscoveryError(target_id, kind, exc.code),)
            )
        except _DiscoveryFailureError:
            return DiscoveryResult(
                errors=(DiscoveryError(target_id, kind, "discovery_failed"),)
            )
        return DiscoveryResult(data=data)

    if cache is not None:
        return await cache.discover(target_id, _discover_once, kind=kind)
    return await _discover_once(target_id)


async def _load_and_normalize(
    target_id: str,
    kind: str,
    kwargs: dict[str, Any],
) -> tuple[InteropCapability, ...]:
    """Load and normalize one target, hiding unexpected adapter details."""
    try:
        tools = await load_external_mcp_tools(target_id, **kwargs)
        return normalize_capabilities(target_id, tools, kind=kind)
    except (InteropMCPError, InteropCapabilityError):
        raise
    except Exception:
        raise _DiscoveryFailureError from None


__all__ = [
    "DiscoveryError",
    "DiscoveryResult",
    "InteropCapability",
    "InteropCapabilityError",
    "MAX_CAPABILITIES",
    "MAX_DESCRIPTION_BYTES",
    "MAX_SCHEMA_BYTES",
    "discover_external_mcp_capabilities",
    "normalize_capabilities",
]
