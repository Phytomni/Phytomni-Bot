# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for DeepGenome analyst result summary loaders.

Covers the §8.2 protein-design loader that maps the digital-design
``psap_scores.png`` + ``summary_all.py`` output into the report's
``protein_path`` / ``protein_summary`` / ``protein_legend`` keys.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
    WorkItemPollCallbacks,
    WorkItemPollOptions,
    WorkItemPollRequest,
    derive_workflow_outcome,
    poll_work_item,
)
from mcp_server_phytomni.agents.deep_genome.summary import (
    UnusableAnalysisResultError,
    build_design_work_item_summary,
    build_sub_summary,
)
from mcp_server_phytomni.runtime.artifact_roles import ArtifactManifest

pytestmark = pytest.mark.agent


def test_protein_design_loader_populates_section_keys(
    tmp_path: Path,
) -> None:
    """build_sub_summary for protein_design fills the §8.2 protein keys."""
    results = tmp_path / "g1"
    results.mkdir()
    (results / "psap_scores.png").write_bytes(b"x")
    (results / "g1_protein_design.summary").write_text(
        "design summary Figure 1", encoding="utf-8"
    )
    (results / "g1_protein_design.legend").write_text(
        "design legend Figure 1", encoding="utf-8"
    )

    out = build_sub_summary(
        analysis_type="protein_design_analysis",
        gene_id="g1",
        deepgenome_out=str(tmp_path),
        data={"gene_name": "g1"},
        figure_index=1,
        results_dir=str(results),
    )

    assert out.data["protein_path"].endswith("psap_scores.png")
    assert "design summary" in out.data["protein_summary"]
    assert out.data["protein_legend"]


def test_promoter_design_summary_reads_sorted_nonblank_summaries(
    tmp_path: Path,
) -> None:
    """Join nonblank promoter summaries in filename order."""
    (tmp_path / "z.summary").write_text("zeta", encoding="utf-8")
    (tmp_path / "a.summary").write_text(" ", encoding="utf-8")
    (tmp_path / "b.summary").write_text("alpha", encoding="utf-8")

    summary = build_design_work_item_summary("promoter_design", tmp_path)

    assert summary.startswith("## Promoter Design")
    assert summary.index("alpha") < summary.index("zeta")
    assert "a.summary" not in summary


def test_promoter_design_summary_falls_back_to_stable_artifact_markdown(
    tmp_path: Path,
) -> None:
    """List promoter artifacts when no summary text is available."""
    (tmp_path / "motif_all_logo.png").write_bytes(b"png")
    (tmp_path / "motifs.legend").write_text("motifs", encoding="utf-8")
    (tmp_path / "z.json").write_text("{}", encoding="utf-8")

    summary = build_design_work_item_summary("promoter_design", tmp_path)

    assert summary.startswith("## Promoter Design")
    assert "motif_all_logo.png" in summary
    assert "motifs.legend" in summary
    assert "z.json" not in summary
    assert summary.strip()


def test_promoter_design_summary_rejects_empty_results(
    tmp_path: Path,
) -> None:
    """Reject a promoter result containing neither text nor artifacts."""
    (tmp_path / "empty.summary").write_text("\n", encoding="utf-8")

    with pytest.raises(UnusableAnalysisResultError):
        build_design_work_item_summary("promoter_design", tmp_path)


@pytest.mark.parametrize("work_item", ["promoter_design", "protein_design"])
@pytest.mark.parametrize(
    "filenames",
    [
        (".phytomni-artifacts.json", "result_files.json"),
        ("arbitrary.json",),
        ("empty.legend",),
        ("motif_all_logo.png", "psap_scores.png"),
    ],
)
def test_design_summary_rejects_inventory_or_empty_outputs(
    tmp_path: Path, work_item: str, filenames: tuple[str, ...]
) -> None:
    """Reject internal inventories and empty files as scientific results."""
    for filename in filenames:
        (tmp_path / filename).write_text(
            "{}" if filename.endswith(".json") else "", encoding="utf-8"
        )
    with pytest.raises(UnusableAnalysisResultError):
        build_design_work_item_summary(work_item, tmp_path)


@pytest.mark.parametrize(
    ("work_item", "image"),
    [
        ("promoter_design", "motif_all_logo.png"),
        ("protein_design", "psap_scores.png"),
    ],
)
def test_design_summary_keeps_supported_scientific_outputs(
    tmp_path: Path, work_item: str, image: str
) -> None:
    """Keep usable visuals and legends without inventory noise."""
    (tmp_path / image).write_bytes(b"synthetic-image")
    (tmp_path / "result.legend").write_text(
        "Observed motifs.", encoding="utf-8"
    )
    manifest = ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "artifacts": [
                {
                    "path": image,
                    "role": "scientific_figure",
                    "media_type": "image/png",
                },
                {
                    "path": "result.legend",
                    "role": "scientific_text",
                    "media_type": "text/plain",
                },
            ],
        }
    )
    (tmp_path / ".phytomni-artifacts.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    report = build_design_work_item_summary(work_item, tmp_path)
    assert image in report
    assert "result.legend" in report
    assert ".json" not in report


@pytest.mark.parametrize("role", ["diagnostic", "execution_log", "input"])
@pytest.mark.parametrize(
    "filename",
    ["diagnostic.legend", "diagnostic.summary", "motif_all_logo.png"],
)
def test_design_summary_rejects_non_scientific_manifest_roles(
    tmp_path: Path, role: str, filename: str
) -> None:
    """A supported filename cannot override an explicit operational role."""
    (tmp_path / filename).write_text("Operational output", encoding="utf-8")
    manifest = ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "artifacts": [
                {"path": filename, "role": role, "media_type": "text/plain"}
            ],
        }
    )
    (tmp_path / ".phytomni-artifacts.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(UnusableAnalysisResultError):
        build_design_work_item_summary("promoter_design", tmp_path)


def test_design_summary_keeps_science_beside_diagnostics(
    tmp_path: Path,
) -> None:
    """Role rejection must not suppress a genuine sibling summary."""
    (tmp_path / "valid.summary").write_text(
        "Observed motifs.", encoding="utf-8"
    )
    (tmp_path / "diagnostic.summary").write_text(
        "Execution failed.", encoding="utf-8"
    )
    manifest = ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "valid.summary",
                    "role": "scientific_text",
                    "media_type": "text/plain",
                },
                {
                    "path": "diagnostic.summary",
                    "role": "diagnostic",
                    "media_type": "text/plain",
                },
            ],
        }
    )
    (tmp_path / ".phytomni-artifacts.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    report = build_design_work_item_summary("promoter_design", tmp_path)
    assert "Observed motifs." in report
    assert "Execution failed." not in report
    assert "diagnostic.summary" not in report


@pytest.mark.parametrize("manifest", ["{}", "not JSON"])
def test_design_summary_rejects_invalid_manifest(
    tmp_path: Path, manifest: str
) -> None:
    """Invalid producer declarations cannot grant scientific admission."""
    (tmp_path / "result.summary").write_text(
        "Unverified output", encoding="utf-8"
    )
    (tmp_path / ".phytomni-artifacts.json").write_text(
        manifest, encoding="utf-8"
    )
    with pytest.raises(UnusableAnalysisResultError):
        build_design_work_item_summary("promoter_design", tmp_path)


async def test_inventory_only_design_leaves_successful_siblings_usable(
    tmp_path: Path,
) -> None:
    """A remote success with no science cannot contaminate sibling results."""
    (tmp_path / "result_files.json").write_text("{}", encoding="utf-8")

    async def remote_status(_task_id: str, _timeout: float) -> dict[str, str]:
        return {"status": "SUCCEEDED"}

    def resolve_result(_submission: RemoteSubmission) -> str:
        return build_design_work_item_summary("promoter_design", tmp_path)

    def transition(
        status: str, summary: str | None, reason: str | None
    ) -> WorkItemOutcome:
        return WorkItemOutcome(status, summary, reason)

    result = await poll_work_item(
        WorkItemPollRequest(
            submission=RemoteSubmission(
                "caller-synthetic", "remote-synthetic", "/obs/test"
            ),
            callbacks=WorkItemPollCallbacks(
                status_reader=remote_status,
                result_resolver=resolve_result,
                transition_sink=transition,
            ),
            options=WorkItemPollOptions(
                request_timeout=4, poll_interval=0, deadline_seconds=10
            ),
        )
    )
    assert result.status == "failed"
    assert result.summary_markdown is None
    assert result.failure_reason == "analysis result resolution failed"
    combined = derive_workflow_outcome(
        [
            {"status": "succeeded", "summary_markdown": "# Valid sibling"},
            {
                "status": result.status,
                "summary_markdown": result.summary_markdown,
            },
        ]
    )
    assert (
        combined.all_terminal and combined.may_synthesize and combined.degraded
    )
    assert (combined.usable_count, combined.unusable_count) == (1, 1)


def test_display_order_keeps_concurrent_summary_numbering_stable(
    tmp_path: Path,
) -> None:
    """Use stable work order rather than completion order for figures."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "gene_tissues.png").write_bytes(b"png")
    (results / "gene_tissues.summary").write_text(
        "Figure 1 tissue", encoding="utf-8"
    )
    (results / "gene_tissues.legend").write_text(
        "Figure 1 legend", encoding="utf-8"
    )

    def build_once(_run: int) -> str:
        built = build_sub_summary(
            "gene_expression_tissues",
            "gene",
            str(tmp_path),
            {"gene_name": "gene"},
            99,
            results_dir=str(results),
            display_order=4,
        )
        return built.data["tissue_summary"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(build_once, (1, 2))

    assert first == second == "Figure 5 tissue"
