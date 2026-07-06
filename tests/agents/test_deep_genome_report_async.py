# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Regression: deep_genome report nodes write files off the event loop."""

import subprocess
import sys


def test_report_nodes_have_no_blocking_open():
    """ruff ASYNC230 must not fire on report.py after the fix."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "src/mcp_server_phytomni/agents/deep_genome/report.py",
            "--select",
            "ASYNC230",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "ASYNC230" not in result.stdout, result.stdout
    assert result.returncode == 0, result.stdout
