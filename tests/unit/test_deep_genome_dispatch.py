# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for DeepGenome analyst result dispatch helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import (
    dispatch as deep_genome_dispatch,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
    DeepGenomeDispatchMixin,
)

pytestmark = pytest.mark.unit


class FakeSensitiveConfig:
    """Minimal sensitive config for OBS credential access."""

    def obs_credentials(self) -> tuple[str, str]:
        """Return fake OBS credentials."""
        return "access-key", "secret-key"

    def is_test_config(self) -> bool:
        """Return whether this is a fake test config."""
        return True


class DispatchHarness(DeepGenomeDispatchMixin):
    """Small concrete harness for private dispatch helper tests."""

    def __init__(self, deepgenome_out: str):
        """Initialize fake DeepGenome config."""
        self.deep_genome_config = SimpleNamespace(
            BUCKET_NAME="phytomni",
            DEEPGENOME_OUT=deepgenome_out,
            OBS_SERVER="https://example.invalid",
        )
        self.sensitive_config = FakeSensitiveConfig()


def test_download_analysis_result_uses_readable_obsfs_dir(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify readable obsfs result directories are used in place."""
    harness = DispatchHarness(str(tmp_path / "local-out"))
    result_dir = tmp_path / "obsfs-result"
    result_dir.mkdir()

    def fail_download(*args: Any, **kwargs: Any):
        del args, kwargs
        raise AssertionError("download_obs_out should not be called")

    monkeypatch.setattr(
        deep_genome_dispatch,
        "obsfs_path_for",
        lambda *args, **kwargs: result_dir,
    )
    monkeypatch.setattr(
        deep_genome_dispatch,
        "download_obs_out",
        fail_download,
    )

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    assert download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
    ) == str(result_dir)


def test_download_analysis_result_falls_back_to_sdk_download(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify unavailable obsfs directories trigger SDK result download."""
    captured: dict[str, Any] = {}
    harness = DispatchHarness(str(tmp_path / "deep-out"))

    def fake_download_obs_out(*args: Any, **kwargs: Any):
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield "keep.txt download succeed."

    monkeypatch.setattr(
        deep_genome_dispatch,
        "download_obs_out",
        fake_download_obs_out,
    )

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    result = download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
    )

    assert result == str(tmp_path / "deep-out" / "GeneA")
    assert captured["kwargs"]["obs_output_path"] == "results/GeneA"
    assert captured["kwargs"]["download_path"] == str(tmp_path / "deep-out")
    assert captured["kwargs"]["bucket_name"] == "phytomni"
    assert captured["kwargs"]["target_file_feature"] == [
        ".png",
        ".summary",
        ".legend",
    ]
