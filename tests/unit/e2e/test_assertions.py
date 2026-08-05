# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline contracts for live terminal-report assertions."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from e2e.helpers import assertions
from e2e.helpers.assertions import (
    assert_deep_genome_terminal,
    assert_terminal_report_and_artifacts,
    fetch_authenticated_result_archive,
)
from tests.unit.e2e.state_fakes import build_task_state


def test_brief_gene_failure_accepts_no_report() -> None:
    """Required-profile failure is terminal without any report."""
    state = replace(
        build_task_state(),
        intermediate_report=None,
        report_stage="waiting_for_brief_gene",
        report_completeness="none",
        report_revision=1,
        brief_gene_status="failed",
        degraded=False,
        degraded_reason=None,
    )

    assert_deep_genome_terminal(state)


def test_post_profile_failure_requires_intermediate_report() -> None:
    """A failure after BriefGene must retain the best report snapshot."""
    state = replace(build_task_state(), intermediate_report=None)

    with pytest.raises(AssertionError):
        assert_deep_genome_terminal(state)


def test_success_requires_final_report_and_artifact_paths() -> None:
    """Successful analyst-class runs need a final report and paths."""
    state = replace(
        build_task_state(),
        status="succeeded",
        intermediate_report="# profile",
        final_report="# final",
        report_stage="final",
        report_completeness="complete",
        report_revision=3,
        degraded=False,
        degraded_reason=None,
        artifacts=({"paths": ("/obs/result.txt",)},),
        output_dirs=("/obs/output",),
    )

    assert_deep_genome_terminal(state)
    assert_terminal_report_and_artifacts(state, needs_artifacts=True)
    assert_terminal_report_and_artifacts(
        replace(state, artifacts=(), output_dirs=()), needs_artifacts=False
    )


def test_artifact_requirement_rejects_directoryless_success() -> None:
    """Design/Network acceptance must not pass on a report alone."""
    state = replace(
        build_task_state(),
        status="succeeded",
        final_report="# final",
        report_stage="final",
        report_completeness="complete",
        report_revision=3,
    )

    with pytest.raises(AssertionError):
        assert_terminal_report_and_artifacts(state, needs_artifacts=True)


def test_result_archive_fetch_uses_public_run_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive fetches resolve from the public umbrella run root."""
    digest_hex = "a" * 64
    observed: dict[str, str] = {}
    monkeypatch.setattr(
        assertions,
        "ServerConfig",
        lambda: SimpleNamespace(
            BUCKET_NAME="test-bucket",
            OBS_SERVER="https://obs.example.test",
        ),
    )

    def get_object_bytes(
        bucket: str, object_ref: str, *, obs_server: str
    ) -> bytes:
        observed.update(
            bucket=bucket,
            object_ref=object_ref,
            obs_server=obs_server,
        )
        return b"archive"

    monkeypatch.setattr(assertions, "get_object_bytes", get_object_bytes)
    run_record = {
        "user_id": "user-1",
        "agent": "network",
        "result": {
            "formatted": {"answer": "Archive ready."},
            "execution": {
                "output_dirs": ["/obs/runs/network-run"],
                "delivery": {
                    "status": "ready",
                    "inventory_digest": f"sha256:{digest_hex}",
                    "archive": {
                        "role": "result_archive",
                        "name": "network-results.zip",
                        "size_bytes": 1,
                    },
                },
            },
        },
    }

    assert (
        fetch_authenticated_result_archive(
            run_record,
            authenticated_user="user-1",
            agent="network",
        )
        == b"archive"
    )
    assert observed == {
        "bucket": "test-bucket",
        "object_ref": (
            f"/obs/runs/network-run/delivery/{digest_hex}/network-results.zip"
        ),
        "obs_server": "https://obs.example.test",
    }
