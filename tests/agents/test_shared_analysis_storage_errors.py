# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Error-path and helper tests for agents/shared/analysis_storage.

Pin the three lookup-failure raises in ``get_data_list`` /
``_get_data_list_cached`` (missing file, missing analysis type,
missing species), the ``ensure_run_output_dir`` early-return branch,
and the ``_obs_error_message`` formatting helper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import mcp_server_phytomni.agents.shared.analysis_storage as st
from mcp_server_phytomni.agents.shared.analysis_storage import (
    _get_data_list_cached,
    _obs_error_message,
    ensure_run_output_dir,
    get_data_list,
)
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity

pytestmark = pytest.mark.unit

_SPECIES_FILE = "src/mcp_server_phytomni/config/species_data_list.json"


def test_get_data_list_raises_filenotfound_with_path_in_message(
    tmp_path,
) -> None:
    """Missing data file surfaces a ``FileNotFoundError`` naming the path."""
    missing = str(tmp_path / "does-not-exist.json")
    with pytest.raises(FileNotFoundError, match=missing):
        get_data_list(missing, "evolution_analysis", "actinidia chinensis")


def test_get_data_list_cached_raises_on_missing_analysis_type() -> None:
    """An unknown analysis type maps to a ``KeyError`` naming the key."""
    with pytest.raises(KeyError, match="unknown-analysis"):
        _get_data_list_cached(
            _SPECIES_FILE,
            "unknown-analysis",
            "actinidia chinensis",
            mtime_ns=0,
            size=0,
        )


def test_get_data_list_cached_raises_on_missing_species() -> None:
    """A real analysis type with an unknown species raises ``KeyError``."""
    with pytest.raises(KeyError, match="not-a-species"):
        _get_data_list_cached(
            _SPECIES_FILE,
            "evolution_analysis",
            "not-a-species",
            mtime_ns=0,
            size=0,
        )


def test_ensure_run_output_dir_reuses_preset_dir() -> None:
    """A non-empty ``output_dir`` short-circuits before touching OBS.

    The reuse path is the contract for warm runs that already minted
    their output directory upstream; it must not call OBS credentials
    on the sensitive config or hit ``create_output_dir``.
    """
    preset = "/obs/phytomni/agent_data/preset/run-X"

    def _credentials_tripwire(*_args: Any, **_kwargs: Any) -> Any:
        """Should never be called on the preset path."""
        raise AssertionError(
            "ensure_run_output_dir reached obs_credentials on the "
            "preset path; the short-circuit at output_dir is broken."
        )

    sensitive_stub = SimpleNamespace(obs_credentials=_credentials_tripwire)
    result = ensure_run_output_dir(
        config=SimpleNamespace(OBS_SERVER="ignored", BUCKET_NAME="ignored"),
        sensitive_config=sensitive_stub,
        task="evolution",
        run_identity=RunIdentity(
            user_id="alice",
            run_id=IdFactory().new_id("run", "evolution"),
            created_at=datetime(2026, 5, 27, tzinfo=UTC),
        ),
        output_dir=preset,
    )

    assert result == preset


def test_create_output_dir_uses_shared_key_when_fingerprint_given(
    monkeypatch,
) -> None:
    """A fingerprint routes the output dir to the content-addressed root."""
    captured = {}

    def _fake_obsfs(
        output_dir: str, bucket_name: str, obsfs_mount_root: str
    ) -> str:
        del obsfs_mount_root
        captured["key"] = output_dir
        return f"/obs/{bucket_name}/{output_dir}"

    monkeypatch.setattr(st, "_create_output_dir_obsfs", _fake_obsfs)
    monkeypatch.setattr(st, "relay_mode_enabled", lambda: False)

    st.create_output_dir(
        user_id="bob",
        task="analyst_task",
        bucket_name="phytomni",
        fingerprint="f" * 64,
    )
    assert captured["key"] == f"agent_data/shared/{'f' * 64}/output/"


def test_obs_error_message_lists_request_id_code_and_message() -> None:
    """The helper renders a 4-line block: header + the 3 OBS error attrs."""
    response = SimpleNamespace(
        requestId="req-abc",
        errorCode="NoSuchKey",
        errorMessage="The specified key does not exist.",
    )

    rendered = _obs_error_message("Put File Failed", response)

    assert rendered.splitlines() == [
        "Put File Failed",
        "requestId: req-abc",
        "errorCode: NoSuchKey",
        "errorMessage: The specified key does not exist.",
    ]
