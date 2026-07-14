# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests that keep repository documentation aligned with public surfaces."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
INLINE_LINK_PATTERN = re.compile(r"!?\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK_PATTERN = re.compile(r"```.*?```", re.DOTALL)
README_TOOL_PATTERN = re.compile(r"\| `([^`]+)`\s+\|")
MCP_TOOL_PATTERN = re.compile(r"\|\s*`([^`]+)`\s*\|\s*(?:sync|async)\s*\|")
ENDPOINT_ROW_PATTERN = re.compile(
    r"\|\s*`(GET|POST)`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE
)


def _git_ls_files(*patterns: str) -> list[Path]:
    """Return git-tracked files matching the supplied pathspecs."""
    result = subprocess.run(
        ["git", "ls-files", *patterns],
        check=True,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def _strip_fenced_blocks(markdown: str) -> str:
    """Remove fenced code blocks before Markdown link extraction."""
    return FENCED_BLOCK_PATTERN.sub("", markdown)


def _is_external_or_anchor(target: str) -> bool:
    """Return True when a Markdown target is not a local file path."""
    return bool(
        target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target)
    )


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    """Resolve a Markdown link target to a local path when applicable."""
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if _is_external_or_anchor(target):
        return None
    target = unquote(target.split("#", 1)[0])
    if not target:
        return None
    return (source.parent / target).resolve()


def _public_tool_names() -> set[str]:
    """Return public MCP tool names from the enum source of truth."""
    return {
        str(member.value)
        for member in PhytomniAgents
        if not member.name.endswith("_DESCRIPTION")
    }


def _documented_endpoint_pairs(path: Path) -> set[tuple[str, str]]:
    """Return endpoint pairs from a Markdown endpoint inventory table."""
    text = path.read_text(encoding="utf-8")
    return set(ENDPOINT_ROW_PATTERN.findall(text))


def _api_endpoint_pairs() -> set[tuple[str, str]]:
    """Return public HTTP API route pairs from the FastAPI app."""
    public_paths = {"/healthz", "/readyz"}
    pairs = set()
    for route in create_app().routes:
        path = getattr(route, "path", "")
        methods: set[str] = set(getattr(route, "methods", ()) or ())
        if not (path in public_paths or path.startswith("/v1/")):
            continue
        for method in methods:
            if method in {"GET", "POST"}:
                pairs.add((method, path))
    # These routes are intentionally absent from the default flag-off app,
    # but remain part of the documented opt-in public surface.
    pairs.update(
        {
            ("GET", "/.well-known/agent-card.json"),
            ("POST", "/a2a"),
            ("GET", "/v1/interop/capabilities"),
            ("GET", "/v1/memories"),
            ("POST", "/v1/memories"),
            ("GET", "/v1/memories/export"),
            ("GET", "/v1/memories/audit"),
            ("GET", "/v1/memories/{memory_id}"),
        }
    )
    return pairs


def test_tracked_markdown_links_resolve_to_local_files() -> None:
    """Verify tracked Markdown relative links point at existing files."""
    failures = []
    markdown_files = [
        path
        for path in _git_ls_files("*.md")
        if not path.relative_to(ROOT).as_posix().startswith("demo_data/docs/")
    ]
    for path in markdown_files:
        text = _strip_fenced_blocks(path.read_text(encoding="utf-8"))
        for raw_target in INLINE_LINK_PATTERN.findall(text):
            target = _local_link_target(path, raw_target)
            if target is not None and not target.exists():
                rel_source = path.relative_to(ROOT).as_posix()
                failures.append(f"{rel_source}: {raw_target}")

    assert not failures


def test_readme_and_mcp_reference_list_public_tools() -> None:
    """Verify public MCP tool docs match the enum source of truth."""
    tool_names = _public_tool_names()

    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_tools = {
        match
        for match in README_TOOL_PATTERN.findall(readme_text)
        if match.endswith("Agent") or match == "GetTaskStatus"
    }

    reference_text = (ROOT / "docs/reference/mcp-tools.md").read_text(
        encoding="utf-8"
    )
    reference_tools = set(MCP_TOOL_PATTERN.findall(reference_text))

    assert readme_tools == tool_names
    assert reference_tools == tool_names


def test_http_docs_list_public_fastapi_routes() -> None:
    """Verify HTTP reference docs list every public FastAPI route."""
    route_pairs = _api_endpoint_pairs()

    assert (
        _documented_endpoint_pairs(ROOT / "docs/reference/http-api.md")
        == route_pairs
    )
    assert (
        _documented_endpoint_pairs(ROOT / "docs/ops/http-api-runbook.md")
        == route_pairs
    )


def test_cli_reference_covers_console_scripts() -> None:
    """Verify CLI docs cover every installed console script."""
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = set(pyproject["project"]["scripts"])
    cli_text = (ROOT / "docs/reference/cli.md").read_text(encoding="utf-8")

    missing = [
        script for script in sorted(scripts) if f"`{script}`" not in cli_text
    ]

    assert missing == []


def test_readme_matches_the_current_interoperability_boundary() -> None:
    """README must distinguish opt-in delegation from default behavior."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    route_paths = {getattr(route, "path", "") for route in create_app().routes}

    assert "/a2a" not in route_paths
    assert re.search(
        r"\|\s*Calls to external MCP tools or A2A agents\s*\|\s*"
        r"Explicit opt-in from Research/Design\s*\|",
        readme,
    )
    assert re.search(
        r"\|\s*User-scoped memory CRUD API\s*\|\s*"
        r"Opt-in; bounded read-only recall\s*\|",
        readme,
    )
    assert "PHYTOMNI_INTEROP_ENABLED" in readme
    assert "/v1/interop/capabilities" in readme
    assert re.search(
        r"\|\s*A2A Agent Card and `/a2a` server\s*\|\s*Opt-in core\s*\|",
        readme,
    )
    public_schemas = (
        ROOT / "src/mcp_server_phytomni/mcp/schemas.py"
    ).read_text(encoding="utf-8")
    # C4.7 keeps the opt-in request controls aligned with the two public
    # delegation-capable tools; the default remains local-only.
    assert (
        public_schemas.count(
            'interop_mode: Literal["off", "auto", "required"]'
        )
        == 2
    )
    assert public_schemas.count("interop_targets: list[str]") == 2
    # C5.1-C5.4 define the domain, local store, and opt-in HTTP surface.
    assert (ROOT / "src/mcp_server_phytomni/runtime/memory/models.py").exists()
    assert (ROOT / "src/mcp_server_phytomni/runtime/memory/sqlite.py").exists()


def test_memory_lifecycle_docs_cover_privacy_and_retention_boundaries() -> (
    None
):
    """Memory docs state the explicit-write and local-retention contract."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    http_api = (ROOT / "docs/reference/http-api.md").read_text(
        encoding="utf-8"
    )
    runbook = (ROOT / "docs/ops/http-api-runbook.md").read_text(
        encoding="utf-8"
    )
    architecture = (ROOT / "docs/explanation/architecture.md").read_text(
        encoding="utf-8"
    )
    upgrading = (ROOT / "docs/ops/upgrading.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, http_api, runbook, architecture, upgrading))

    for phrase in (
        "read-only recall",
        "TTL",
        "memory_mutation_audit",
        "no autonomous",
        "no embedding",
        "local SQLite",
        "MEMORY_ENABLED",
        "explicit user memory",
    ):
        assert phrase.lower() in combined.lower()

    assert (
        "do not configure or advertise those outbound and memory surfaces"
        not in upgrading
    )
