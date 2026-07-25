# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FileUploadResponse.path is derived from obs_path, not hand-written.

Pins that path is a computed read-only mirror of obs_path so the two
can never drift, while model_dump still emits both keys for the
OpenAI-files-compatible shape.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.schemas import FileUploadResponse

pytestmark = pytest.mark.server


def _make(obs_path: str) -> FileUploadResponse:
    return FileUploadResponse(
        id="upload_x",
        bytes=3,
        filename="f.txt",
        purpose="agent_context",
        created_at=1,
        obs_path=obs_path,
    )


def test_path_mirrors_obs_path_in_dump() -> None:
    """model_dump emits both keys and path equals obs_path."""
    dumped = _make("/obs/b/k").model_dump()
    assert dumped["obs_path"] == "/obs/b/k"
    assert dumped["path"] == "/obs/b/k"
    assert set(dumped) >= {"obs_path", "path"}


def test_path_arg_is_ignored_in_favor_of_obs_path() -> None:
    """A stray path key in input data cannot override obs_path value."""
    obj = FileUploadResponse.model_validate(
        {
            "id": "upload_x",
            "bytes": 3,
            "filename": "f.txt",
            "purpose": "agent_context",
            "created_at": 1,
            "obs_path": "/obs/b/k",
            "path": "/obs/other/mismatch",
        }
    )
    assert obj.model_dump()["path"] == "/obs/b/k"


def test_path_not_settable_in_dump() -> None:
    """path is not a writable field; obs_path drives the dumped value."""
    obj = _make("/obs/a")
    dumped = obj.model_dump()
    assert dumped["path"] == "/obs/a"
    obj2 = obj.model_copy(update={"obs_path": "/obs/c"})
    assert obj2.model_dump()["path"] == "/obs/c"


def test_dataset_purpose_is_an_allowed_upload_value() -> None:
    """Structured dataset uploads use the additive purpose value."""
    response = FileUploadResponse(
        id="upload_csv",
        bytes=3,
        filename="data.csv",
        purpose="dataset",
        created_at=1,
        obs_path="/obs/b/data.csv",
    )

    assert response.purpose == "dataset"
