# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Opaque HTTP input bag for durable Research admission."""

from __future__ import annotations

from typing import Any


class ResearchHttpAdmissionInput:
    """Preserve flat HTTP fields in one detached immutable-shaped bag."""

    __slots__ = ("_values",)

    def __init__(self, **values: Any) -> None:
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        """Return a detached mapping for diagnostics and tests."""
        return dict(self._values)

    def get(self, name: str, default: Any = None) -> Any:
        """Read one optional value without raising for malformed input."""
        return self._values.get(name, default)


__all__ = ["ResearchHttpAdmissionInput"]
