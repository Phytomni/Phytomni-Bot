# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Generated artifacts stay isolated without hiding reviewable source."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _module():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/check_generated_artifacts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_generated_artifacts", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_path_classifier_is_path_scoped() -> None:
    helper = _module()
    for path in (
        ".gocache/00/cache-entry",
        ".codex-runtime/service/server.pid",
        ".codex-test-cache/vitest-results.json",
        "apps/web/node_modules/.vite/deps/chunk.js",
        "apps/web/coverage/index.html",
        "apps/server/test-results/result.json",
    ):
        assert helper.is_generated_path(path), path
    for path in (
        "src/mcp_server_phytomni/runtime/new_source.py",
        "migrations/20260824_dispatch_integrity.sql",
        "docs/contracts/execution-runtime/v2/fixture.json",
        "tests/fixtures/generated-looking.json",
        "uv.lock",
        "package-lock.json",
        "go.sum",
    ):
        assert not helper.is_generated_path(path), path


def test_inventory_separates_business_source_and_rejects_tracked_cache(
    tmp_path: Path,
) -> None:
    helper = _module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("/.gocache/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    generated = tmp_path / ".gocache/cache-entry"
    generated.parent.mkdir()
    generated.write_text("cache", encoding="utf-8")
    source = tmp_path / "src/new_source.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")

    inventory = helper.generated_artifact_inventory(tmp_path)
    assert inventory["tracked_generated"] == []
    assert inventory["unignored_generated"] == []
    assert inventory["untracked_business_source"] == ["src/new_source.py"]

    subprocess.run(
        ["git", "add", "-f", ".gocache/cache-entry"],
        cwd=tmp_path,
        check=True,
    )
    inventory = helper.generated_artifact_inventory(tmp_path)
    assert inventory["tracked_generated"] == [".gocache/cache-entry"]
    assert helper.generated_artifact_violations(tmp_path) == [
        "tracked generated artifact: .gocache/cache-entry"
    ]
