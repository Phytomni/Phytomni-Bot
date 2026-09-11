# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline pytest configuration precedes optional native imports."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_bootstrap_disables_native_telemetry_before_import(
    tmp_path: Path,
) -> None:
    """ONNX Runtime must not create telemetry artifacts during collection."""
    root = Path(__file__).resolve().parents[2]
    script = (
        "import os, runpy, sys\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "runpy.run_path(sys.argv[1])\n"
        "assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'\n"
        "import onnxruntime\n"
        "from pathlib import Path\n"
        "assert not list(Path.cwd().iterdir())\n"
    )
    env = dict(os.environ, ORT_DISABLE_TELEMETRY="0")
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / "conftest.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
