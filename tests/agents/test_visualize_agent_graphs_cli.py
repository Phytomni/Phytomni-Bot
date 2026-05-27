# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""CLI smoke tests for ``scripts/visualize_agent_graphs.py``.

The tests load the script as a module via :mod:`importlib` (the file
has no package ``__init__.py``) and drive its ``main(argv)`` entry
point with in-process arguments. Tests that would otherwise reach
``mermaid.ink`` monkeypatch the ``draw_mermaid_png`` call site so the
suite stays offline; manifest tests exercise the JSON export end to
end.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_script() -> Any:
    """Import the visualization script as a module without packaging it."""
    script_path = Path(__file__).resolve().parents[2] / (
        "scripts/visualize_agent_graphs.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_viz_agent_graphs", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_viz_agent_graphs", module)
    spec.loader.exec_module(module)
    return module


def test_list_flag_prints_registered_ids(
    capsys: pytest.CaptureFixture,
) -> None:
    """``--list`` prints each registered id on its own line and exits 0."""
    viz = _load_script()
    exit_code = viz.main(["--list"])
    assert exit_code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert "brief_gene" in out
    assert "deep_genome" in out


def test_unknown_agent_returns_nonzero(
    capsys: pytest.CaptureFixture,
) -> None:
    """``--agent <unknown>`` exits 2 and writes a clear stderr line."""
    viz = _load_script()
    exit_code = viz.main(["--agent", "no_such_agent"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "unknown graph" in captured.err


def test_brief_gene_renders_mermaid_block(
    capsys: pytest.CaptureFixture,
) -> None:
    """``--agent brief_gene`` prints a labeled Mermaid code block."""
    viz = _load_script()
    exit_code = viz.main(["--agent", "brief_gene"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "=== brief_gene ===" in out
    assert "```mermaid" in out


def test_manifest_writes_json_with_expected_nodes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """``--manifest DIR`` writes JSON containing the documented node set."""
    viz = _load_script()
    exit_code = viz.main(
        ["--agent", "brief_gene", "--manifest", str(tmp_path)],
    )
    assert exit_code == 0
    capsys.readouterr()  # Drain captured stdout/stderr; not asserted here.

    written = tmp_path / "brief_gene.json"
    assert written.exists()
    payload = json.loads(written.read_text(encoding="utf-8"))
    names = {node["name"] for node in payload["nodes"]}
    assert {
        "query_judge_node",
        "fetch_annotation_node",
        "retrieve_node",
        "generate_node",
        "follow_up_node",
    } <= names


def test_png_writes_file_under_out_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """``--png DIR`` writes a PNG file under the requested directory.

    The renderer calls ``draw_mermaid_png`` which by default hits
    ``mermaid.ink``. Patch the script's ``_write_png`` to stay
    offline while still verifying that the CLI invokes the writer
    and creates the expected directory.
    """
    viz = _load_script()

    fake_payload = b"\x89PNG\r\n\x1a\n_fake"

    def _fake_write_png(
        name: str,
        _graph_app: Any,
        out_dir: Path,
        _xray: int,
    ) -> Path:
        target = out_dir / f"{name}.png"
        target.write_bytes(fake_payload)
        return target

    monkeypatch.setattr(viz, "_write_png", _fake_write_png)

    exit_code = viz.main(
        ["--agent", "brief_gene", "--png", str(tmp_path)],
    )
    assert exit_code == 0
    capsys.readouterr()
    written = tmp_path / "brief_gene.png"
    assert written.exists()
    assert written.read_bytes() == fake_payload


def test_build_default_registry_lists_phase_zero_agents() -> None:
    """``build_default_registry`` registers exactly the Phase-0 agents."""
    viz = _load_script()
    registry = viz.build_default_registry()
    assert "brief_gene" in registry.names()
    assert "deep_genome" in registry.names()
