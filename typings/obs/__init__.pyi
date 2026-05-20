# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)

"""Minimal type stubs for the OBS SDK surface used by Phytomni.

Pylint targets executable code semantics; this file is a type stub
mirroring an external SDK's interface, so several pylint rules are
structurally unsatisfiable here:

* unused-argument — every parameter is "unused" because stubs have no
  bodies, and names must mirror the real OBS SDK API.
* too-few-public-methods — response classes are dataclass-like field
  declarations, not behavior-bearing classes.
* invalid-name / missing-function-docstring — the OBS SDK uses
  camelCase method and argument names and the type signatures are the
  documentation.
* too-many-arguments / too-many-positional-arguments — the upstream
  SDK methods take 6-7 positional arguments; the stub must match.

mypy and pyright are the canonical type-stub checkers and continue to
enforce correctness here; pylint runs to catch regressions in the
stub's exported names and import graph.
"""

from typing import Any

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

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        server: str,
        **kwargs: Any,
    ) -> None: ...
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
