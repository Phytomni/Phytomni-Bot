# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)

"""Minimal type stubs for the OBS SDK surface used by Phytomni.

mypy and pyright are the canonical stub checkers and validate these
declarations. The mixedCase identifiers (e.g. ``getObject``,
``bucketName``, ``requestId``) mirror the upstream OBS SDK API exactly
so the type-check correspondence holds. Ruff silences the PEP 8 naming
rules that target executable-code semantics (N802 / N803 / N815) on
``typings/**/*.pyi`` via ``[tool.ruff.lint.per-file-ignores]``;
structural ruff checks (import sort, dead code) still run on this
file. Pylint uses the file-local rule mask below because these six
diagnostics describe the external stub surface rather than executable
implementation quality.
"""

import ssl
from typing import Any

# The names and signatures mirror the external OBS SDK exactly.
# pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613

class ObsResponse:
    """Common OBS response fields used by this project."""

    status: int
    requestId: str
    errorCode: str
    errorMessage: str
    body: Any

class ObjectSummary:
    """OBS object summary returned by listObjects."""

    key: str

class ListObjectsBody:
    """Subset of listObjects body fields used for pagination."""

    contents: list[ObjectSummary]
    is_truncated: bool
    next_marker: str | None

class ListObjectsResponse(ObsResponse):
    """OBS listObjects response shape used by download helpers."""

    body: ListObjectsBody

class PutObjectHeader:
    """Header object accepted by OBS put APIs."""

    contentType: str

class GetObjectHeader:
    """Header object accepted by OBS get APIs."""

    if_modified_since: str

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
    ) -> None: ...
    def _init_ssl_context(self, custom_ciphers: str | None) -> None: ...
    def downloadFile(
        self,
        bucketName: str,
        objectKey: str,
        downloadFile: str,
        partSize: int,
        taskNum: int,
        enableCheckpoint: bool,
        **kwargs: Any,
    ) -> ObsResponse: ...
    def putFile(
        self,
        bucketName: str,
        objectKey: str,
        file_path: str,
        metadata: dict[str, str] | None = ...,
        headers: PutObjectHeader | None = ...,
        **kwargs: Any,
    ) -> ObsResponse: ...
    def deleteObject(
        self,
        bucketName: str,
        objectKey: str,
        **kwargs: Any,
    ) -> ObsResponse: ...
    def putContent(
        self,
        bucketName: str,
        objectKey: str,
        content: Any,
        **kwargs: Any,
    ) -> ObsResponse: ...
    def listObjects(
        self,
        bucketName: str,
        prefix: str,
        marker: str | None = ...,
        max_keys: int | None = ...,
        encoding_type: str | None = ...,
        **kwargs: Any,
    ) -> ListObjectsResponse: ...
    def getObject(
        self,
        bucketName: str,
        objectKey: str,
        downloadPath: str,
        headers: GetObjectHeader | None = ...,
        **kwargs: Any,
    ) -> ObsResponse: ...
