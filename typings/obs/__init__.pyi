# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)

"""Minimal type stubs for the OBS SDK surface used by Phytomni.

mypy and pyright are the canonical stub checkers and validate these
declarations. The mixedCase identifiers (e.g. ``getObject``,
``bucketName``, ``requestId``) mirror the upstream OBS SDK API exactly
so the type-check correspondence holds. The SDK's mixedCase surface is
represented through typed dynamic attributes below, keeping those external
names available without making the stub itself violate repository naming
rules.
"""

import ssl
from dataclasses import dataclass
from typing import Any

# The names and signatures mirror the external OBS SDK exactly.

@dataclass(init=False)
class ObsResponse:
    """Common OBS response fields used by this project."""

    status: int
    body: Any

    def __getattr__(self, name: str) -> Any: ...

@dataclass(init=False)
class ObjectSummary:
    """OBS object summary returned by listObjects."""

    key: str

@dataclass(init=False)
class ListObjectsBody:
    """Subset of listObjects body fields used for pagination."""

    contents: list[ObjectSummary]
    is_truncated: bool
    next_marker: str | None

@dataclass(init=False)
class ListObjectsResponse(ObsResponse):
    """OBS listObjects response shape used by download helpers."""

    body: ListObjectsBody

@dataclass(init=False)
class PutObjectHeader:
    """Header object accepted by OBS put APIs."""

    def __getattr__(self, name: str) -> Any: ...
    def __setattr__(self, name: str, value: Any) -> None: ...

@dataclass(init=False)
class GetObjectHeader:
    """Header object accepted by OBS get APIs."""

    if_modified_since: str

@dataclass(init=False)
class ObsClient:
    """Subset of the OBS client methods used by Phytomni."""

    ssl_verify: bool | str
    context: ssl.SSLContext | None

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        server: str,
        **kwargs: Any,
    ) -> None:
        _ = (access_key_id, secret_access_key, server, kwargs)

    def _init_ssl_context(self, custom_ciphers: str | None) -> None:
        _ = custom_ciphers

    def __getattr__(self, name: str) -> Any: ...
