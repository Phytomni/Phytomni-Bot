# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Inventory and evaluation helpers for production coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import scripts.check_module_coverage as coverage_gate

pytestmark = pytest.mark.unit


def test_tracked_production_files_stay_inside_packaged_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git inventory keeps only packaged Python sources."""

    def fake_run(*_args: object, **_kwargs: object) -> Any:
        return type(
            "Completed",
            (),
            {
                "stdout": "\n".join(
                    (
                        "src/mcp_server_phytomni/api/app.py",
                        "src/mcp_client_phytomni/client.py",
                        "scripts/check_module_coverage.py",
                        "e2e/test_data_agent_e2e.py",
                        "typings/obs.pyi",
                        "src/mcp_server_phytomni/README.md",
                    )
                )
            },
        )()

    monkeypatch.setattr(coverage_gate.subprocess, "run", fake_run)
    tracked = coverage_gate._tracked_production_files()
    assert tracked == {
        "src/mcp_server_phytomni/api/app.py",
        "src/mcp_client_phytomni/client.py",
    }


def test_evaluate_production_classifies_missing_zero_and_violations() -> None:
    """The diagnostic evaluator reports inventory gaps without changing floors."""
    files = {
        "src/mcp_server_phytomni/ok.py": {
            "summary": {"percent_covered": 80.0, "num_statements": 10}
        },
        "src/mcp_server_phytomni/exact.py": {
            "summary": {"percent_covered": 80.0, "num_statements": 4}
        },
        "src/mcp_server_phytomni/low_lines.py": {
            "summary": {"percent_covered": 79.9, "num_statements": 20}
        },
        "src/mcp_server_phytomni/empty.py": {
            "summary": {"percent_covered": 100.0, "num_statements": 0}
        },
    }
    expected = {
        "src/mcp_server_phytomni/ok.py",
        "src/mcp_server_phytomni/exact.py",
        "src/mcp_server_phytomni/low_lines.py",
        "src/mcp_server_phytomni/empty.py",
        "src/mcp_server_phytomni/absent.py",
    }
    result = coverage_gate._evaluate_production(files, expected)
    assert result.checked == 3
    assert result.missing == ["src/mcp_server_phytomni/absent.py"]
    assert result.zero_statement == ["src/mcp_server_phytomni/empty.py"]
    assert result.violations == ["src/mcp_server_phytomni/low_lines.py"]


def test_load_coverage_rejects_missing_and_malformed_reports(
    tmp_path: Path,
) -> None:
    """Malformed coverage JSON stays a controlled exit-2 diagnostic."""
    missing = tmp_path / "absent.json"
    with pytest.raises(SystemExit) as missing_info:
        coverage_gate._load_coverage(missing)
    assert missing_info.value.code == 2

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit) as invalid_info:
        coverage_gate._load_coverage(invalid)
    assert invalid_info.value.code == 2

    no_files = tmp_path / "no-files.json"
    no_files.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as no_files_info:
        coverage_gate._load_coverage(no_files)
    assert no_files_info.value.code == 2
