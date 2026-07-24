# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Narrow compatibility boundary for the third-party OBS SDK import.

The currently available ``esdk-obs-python`` releases contain an invalid
escape in ``obs.const``. The repository treats warnings as errors, so the
vendor module cannot be imported on Python 3.13 or 3.14 without a narrowly
scoped compatibility boundary. Only that exact vendor warning is contained;
all other warnings continue to propagate as errors.
"""

from __future__ import annotations

import warnings
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from obs import GetObjectHeader, ObsClient, PutObjectHeader

__all__ = ["GetObjectHeader", "ObsClient", "PutObjectHeader"]

_VENDOR_WARNING_MESSAGE = r"invalid escape sequence.*"
_VENDOR_WARNING_MODULE = r"^obs\.const$"


def _load_sdk_symbols() -> dict[str, type[Any]]:
    """Load the OBS symbols while containing only its known syntax warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_VENDOR_WARNING_MESSAGE,
            category=SyntaxWarning,
            module=_VENDOR_WARNING_MODULE,
        )
        sdk = import_module("obs")
    return {
        "ObsClient": cast("type[Any]", sdk.ObsClient),
        "GetObjectHeader": cast("type[Any]", sdk.GetObjectHeader),
        "PutObjectHeader": cast("type[Any]", sdk.PutObjectHeader),
    }


_SDK_SYMBOLS = _load_sdk_symbols()


def __getattr__(name: str) -> Any:
    """Resolve exported SDK classes without redefining their type aliases."""
    try:
        return _SDK_SYMBOLS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
