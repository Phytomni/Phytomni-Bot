# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Feature-gated loading for operator-owned interop target configuration.

Classes: InteropRegistry, InteropRegistryError.
Functions: load_interop_registry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import SecretStr, TypeAdapter, ValidationError

from ..config.defaults import ApiConfig
from ..config.settings import SensitiveConfig
from .credentials import InteropCredentialError, credential_references
from .models import InteropTarget

_TARGETS_ADAPTER = TypeAdapter(list[InteropTarget])


class InteropRegistryError(ValueError):
    """Raised when enabled operator interop configuration is invalid."""


@dataclass(frozen=True, slots=True)
class InteropRegistry:
    """Immutable, secret-free lookup of validated interop targets."""

    enabled: bool
    _targets: Mapping[str, InteropTarget] = field(repr=False)

    def __post_init__(self) -> None:
        """Copy/freeze lookup state and enforce construction invariants."""
        copied_targets = dict(self._targets)
        if not self.enabled and copied_targets:
            raise InteropRegistryError(
                "disabled interop registry cannot contain targets"
            )
        for mapping_key, target in copied_targets.items():
            if mapping_key != target.id:
                raise InteropRegistryError(
                    "interop registry mapping key must match target id"
                )
        object.__setattr__(
            self,
            "_targets",
            MappingProxyType(copied_targets),
        )

    @classmethod
    def disabled(cls) -> InteropRegistry:
        """Return an inert registry without parsing configuration."""
        return cls(enabled=False, _targets=MappingProxyType({}))

    def target_ids(self) -> tuple[str, ...]:
        """Return configured target ids in deterministic order."""
        return tuple(sorted(self._targets))

    def require_target(self, target_id: str) -> InteropTarget:
        """Resolve one operator target by id without accepting endpoints."""
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise InteropRegistryError(
                f"unknown interop target id: {target_id}"
            ) from exc


def _parse_targets(raw: SecretStr) -> list[InteropTarget]:
    """Parse and validate target JSON with redacted failure messages."""
    try:
        payload = json.loads(raw.get_secret_value())
    except (json.JSONDecodeError, TypeError):
        raise InteropRegistryError(
            "INTEROP_TARGETS must be valid JSON"
        ) from None
    try:
        return _TARGETS_ADAPTER.validate_python(payload)
    except ValidationError:
        raise InteropRegistryError(
            "INTEROP_TARGETS contains an invalid target"
        ) from None


def _credential_references(secret_json: SecretStr) -> frozenset[str]:
    """Validate secret JSON shape and return names without secret values."""
    try:
        return credential_references(secret_json)
    except InteropCredentialError as exc:
        raise InteropRegistryError(str(exc)) from None


def _build_registry(
    targets: list[InteropTarget], credential_refs: frozenset[str]
) -> InteropRegistry:
    """Validate cross-target invariants and freeze the lookup mapping."""
    by_id: dict[str, InteropTarget] = {}
    for target in targets:
        if target.id in by_id:
            raise InteropRegistryError(f"duplicate target id: {target.id}")
        if (
            target.credential_ref is not None
            and target.credential_ref not in credential_refs
        ):
            raise InteropRegistryError(
                "missing credential reference: " f"{target.credential_ref}"
            )
        by_id[target.id] = target
    return InteropRegistry(
        enabled=True,
        _targets=MappingProxyType(by_id),
    )


def load_interop_registry(
    api_config: ApiConfig | None = None,
    sensitive_config: SensitiveConfig | None = None,
) -> InteropRegistry:
    """Load the interop registry only when its feature flag is enabled.

    Disabled mode returns before target JSON or sensitive settings are read.
    Enabled mode validates local configuration only; it never resolves DNS,
    connects to a peer, or checks whether a stdio command exists.

    Args:
        api_config: Optional non-secret API configuration.
        sensitive_config: Optional preloaded secret configuration for tests or
            callers that already own the process-cached instance.

    Returns:
        An inert disabled registry or an enabled validated target registry.

    Raises:
        InteropRegistryError: If enabled target or credential JSON is invalid.
    """
    resolved_api_config = api_config or ApiConfig()
    if not resolved_api_config.INTEROP_ENABLED:
        return InteropRegistry.disabled()
    resolved_sensitive_config = sensitive_config or SensitiveConfig.load()
    targets = _parse_targets(resolved_api_config.INTEROP_TARGETS)
    credential_refs = _credential_references(
        resolved_sensitive_config.INTEROP_CREDENTIALS
    )
    return _build_registry(targets, credential_refs)


__all__ = [
    "InteropRegistry",
    "InteropRegistryError",
    "load_interop_registry",
]
