# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Exercise the offline CLI without the application's credential bootstrap."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "build_curated_gene_bundle.py"
)


def test_cli_build_and_check_without_importing_application(tmp_path):
    """Run both CLI operations while forbidding application imports."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "report.md").write_bytes(b"# Approved gene report\r\n")
    inputs = tmp_path / "approved.json"
    inputs.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gene_id": "AT1G01010",
                "report_file": "AT1G01010_result.md",
                "report_source": "report.md",
                "reference_count": 0,
                "resources": [],
                "reference_materials": [],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    # A fresh process blocks all package imports before the entry point runs.
    probe = """
import importlib.abc
import runpy
import sys
class RejectApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('mcp_server_phytomni'):
            raise AssertionError('offline compiler imported application')
sys.meta_path.insert(0, RejectApplication())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    for args in (
        [
            "build",
            "--input",
            str(inputs),
            "--source-root",
            str(source),
            "--output-root",
            str(output),
        ],
        ["check", "--bundle-root", str(output), "--gene-id", "AT1G01010"],
    ):
        result = subprocess.run(
            [sys.executable, "-c", probe, str(SCRIPT), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "no publication performed" in result.stdout
        assert "mcp_server_phytomni" not in result.stderr


def test_cli_failure_does_not_expose_input_path(tmp_path, capsys):
    """Return a stable CLI error without printing the unavailable path."""
    spec = importlib.util.spec_from_file_location("curated_bundle_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert (
        script.main(
            [
                "build",
                "--input",
                str(tmp_path / "private-input.json"),
                "--source-root",
                str(tmp_path),
                "--output-root",
                str(tmp_path / "out"),
            ]
        )
        == 1
    )
    assert (
        capsys.readouterr().err
        == "Curated bundle rejected: input_unavailable\n"
    )
