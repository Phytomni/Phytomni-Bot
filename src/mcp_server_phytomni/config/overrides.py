# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for building config copies from wrapper arguments."""

from collections.abc import Mapping
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, SecretStr

ConfigT = TypeVar("ConfigT", bound=BaseModel)
FieldMap = Mapping[str, str]


def collect_mapped_overrides(
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    skip_none: bool = True,
) -> dict[str, Any]:
    """Collect non-secret config updates from wrapper argument names."""
    updates: dict[str, Any] = {}
    for source_key, target_key in field_map.items():
        if source_key not in values:
            continue
        value = values[source_key]
        if skip_none and value is None:
            continue
        updates[target_key] = value
    return updates


def copy_config_with_overrides(
    base_config: ConfigT,
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    fixed_updates: Optional[Mapping[str, Any]] = None,
    skip_none: bool = True,
) -> ConfigT:
    """Return a config copy with mapped public wrapper overrides."""
    updates = _collect_fixed_updates(fixed_updates, skip_none=skip_none)
    updates.update(
        collect_mapped_overrides(
            values,
            field_map,
            skip_none=skip_none,
        )
    )
    return base_config.model_copy(update=updates)


def copy_sensitive_config_with_overrides(
    base_config: ConfigT,
    values: Mapping[str, Any],
    *,
    field_map: Optional[FieldMap] = None,
    secret_field_map: Optional[FieldMap] = None,
    fixed_updates: Optional[Mapping[str, Any]] = None,
    skip_none: bool = True,
) -> ConfigT:
    """Return a sensitive config copy with secret fields kept separate."""
    updates = _collect_fixed_updates(fixed_updates, skip_none=skip_none)
    updates.update(
        collect_mapped_overrides(
            values,
            field_map or {},
            skip_none=skip_none,
        )
    )
    updates.update(
        _collect_secret_overrides(
            values,
            secret_field_map or {},
            skip_none=skip_none,
        )
    )
    return base_config.model_copy(update=updates)


def _collect_fixed_updates(
    fixed_updates: Optional[Mapping[str, Any]],
    *,
    skip_none: bool,
) -> dict[str, Any]:
    if fixed_updates is None:
        return {}
    return {
        key: value
        for key, value in fixed_updates.items()
        if not (skip_none and value is None)
    }


def _collect_secret_overrides(
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    skip_none: bool,
) -> dict[str, SecretStr]:
    updates: dict[str, SecretStr] = {}
    for source_key, target_key in field_map.items():
        if source_key not in values:
            continue
        value = values[source_key]
        if skip_none and value is None:
            continue
        updates[target_key] = _to_secret_str(value)
    return updates


def _to_secret_str(value: Any) -> SecretStr:
    if isinstance(value, SecretStr):
        return value
    return SecretStr(str(value))
