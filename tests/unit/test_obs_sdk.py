# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the narrow third-party OBS import compatibility boundary."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import warnings
from types import SimpleNamespace

import pytest

from mcp_server_phytomni.storage import obs_sdk

pytestmark = pytest.mark.unit


def test_obs_sdk_import_is_clean_under_strict_warnings() -> None:
    """The vendor import survives strict warnings without global filtering."""
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "error"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from mcp_server_phytomni.storage.obs_sdk import "
                "GetObjectHeader, ObsClient, PutObjectHeader\n"
                "assert ObsClient and GetObjectHeader and PutObjectHeader"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=os.getcwd(),
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_obs_sdk_boundary_does_not_hide_unrelated_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warnings outside the exact vendor message remain errors."""
    sdk = SimpleNamespace(
        ObsClient=object,
        GetObjectHeader=object,
        PutObjectHeader=object,
    )

    def import_with_warning(_name: str) -> SimpleNamespace:
        warnings.warn_explicit(
            "different warning",
            SyntaxWarning,
            filename="/vendor/obs/const.py",
            lineno=1,
            module="obs.const",
        )
        return sdk

    monkeypatch.setattr(importlib, "import_module", import_with_warning)
    try:
        with pytest.raises(SyntaxWarning, match="different warning"):
            importlib.reload(obs_sdk)
    finally:
        monkeypatch.undo()
        importlib.reload(obs_sdk)
