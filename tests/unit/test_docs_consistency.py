# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests that keep repository documentation aligned with public surfaces."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from tests.support.markdown import parse_bold_records

from mcp_server_phytomni.api.a2a.card import build_agent_card
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
_ARCHITECTURE_DOC_PATHS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs/reference/http-api.md",
    ROOT / "docs/reference/cli.md",
    ROOT / "docs/reference/mcp-tools.md",
    ROOT / "docs/ops/http-api-runbook.md",
    ROOT / "docs/explanation/architecture.md",
)
# AGENTS.md is local-only and ignored by Git; clean checkouts must still
# validate every durable, tracked documentation surface.
ARCHITECTURE_DOCS = tuple(
    path for path in _ARCHITECTURE_DOC_PATHS if path.is_file()
)
INLINE_LINK_PATTERN = re.compile(r"!?\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")
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
    """Remove code blocks and spans before Markdown link extraction."""
    without_fences = FENCED_BLOCK_PATTERN.sub("", markdown)
    return INLINE_CODE_PATTERN.sub("", without_fences)


def _read_architecture_docs() -> str:
    """Read the durable guidance surfaces as one consistency corpus."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in ARCHITECTURE_DOCS
    )


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


def _markdown_bullet_records(
    text: str, first_key: str
) -> list[dict[str, str]]:
    """Parse wrapped bold-field records used by the Markdown gate."""
    return parse_bold_records(text.splitlines(), first_key)


def _capability_status(text: str, capability: str, status: str) -> bool:
    """Return whether a capability has the expected release status."""
    table_pattern = re.compile(
        rf"\|\s*{re.escape(capability)}\s*\|\s*" rf"{re.escape(status)}\s*\|"
    )
    bullet_pattern = re.compile(
        rf"-\s+\*\*Capability:\*\*\s*{re.escape(capability)}\s+"
        rf"\*\*0\.1\.3 status:\*\*\s*{re.escape(status)}"
    )
    return bool(table_pattern.search(text) or bullet_pattern.search(text))


def _documented_endpoint_pairs(path: Path) -> set[tuple[str, str]]:
    """Return endpoint pairs from a Markdown table or field inventory."""
    text = path.read_text(encoding="utf-8")
    pairs = set(ENDPOINT_ROW_PATTERN.findall(text))
    pairs.update(
        (record["Method"], record["Path"])
        for record in _markdown_bullet_records(text, "Method")
        if record.get("Method") in {"GET", "POST"} and "Path" in record
    )
    return pairs


def _api_endpoint_pairs() -> set[tuple[str, str]]:
    """Return public HTTP API route pairs from the FastAPI app."""
    public_paths = {"/healthz", "/readyz"}
    pairs = set()

    def iter_routes(routes: Sequence[object]) -> list[object]:
        """Expand newer FastAPI/Starlette included-router wrappers."""
        expanded: list[object] = []
        for route in routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                expanded.extend(iter_routes(original_router.routes))
            else:
                expanded.append(route)
        return expanded

    for route in iter_routes(create_app().routes):
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
        if path.exists()
        and not path.relative_to(ROOT).as_posix().startswith("demo_data/docs/")
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
    readme_tools.update(
        record["Tool"]
        for record in _markdown_bullet_records(readme_text, "Tool")
        if "Tool" in record
    )

    reference_text = (ROOT / "docs/reference/mcp-tools.md").read_text(
        encoding="utf-8"
    )
    reference_tools = set(MCP_TOOL_PATTERN.findall(reference_text))
    reference_tools.update(
        record["Tool"]
        for record in _markdown_bullet_records(reference_text, "Tool")
        if "Tool" in record
    )

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


def test_async_report_docs_distinguish_ack_status_and_terminal_report() -> (
    None
):
    """Keep async submit, polling, and terminal report wording aligned."""
    mcp = (ROOT / "docs/reference/mcp-tools.md").read_text(encoding="utf-8")
    http = (ROOT / "docs/reference/http-api.md").read_text(encoding="utf-8")
    e2e = (ROOT / "e2e/README.md").read_text(encoding="utf-8")
    for text in (mcp, http, e2e):
        assert "submission acknowledgement" in text
        assert "final_report" in text
    assert "does not prove live backend acceptance" in e2e


def test_async_cli_docs_lock_transport_and_exit_codes() -> None:
    """Keep the HTTP CLI contract visible and key-safe."""
    cli = (ROOT / "docs/reference/cli.md").read_text(encoding="utf-8")
    for command in ("submit <agent>", "status <run_id>", "follow <run_id>"):
        assert command in cli
    assert "PHYTOMNI_API_KEY" in cli
    assert "--api-key" not in cli
    assert "0 success, 1 task failure, 2 client error, 3 local timeout" in cli
    assert "does not cancel the remote run" in cli


def test_durable_docs_match_deep_genome_architecture() -> None:
    """Keep durable guidance aligned with shipped DeepGenome boundaries."""
    combined = _read_architecture_docs()
    for phrase in (
        "BriefGene is required",
        "coordinator-owned polling",
        "intermediate_report",
        "report_revision",
        "HTTP-backed CLI",
        "does not resume after process restart",
        "deep_genome_remote_tasks",
    ):
        assert phrase in combined
    for false_claim in (
        "durable DeepGenome worker",
        "DataAgent HTTP streaming is supported",
        "production migration complete",
        "Web integration complete",
    ):
        assert false_claim not in combined


def test_direct_gauss_rollback_uses_prior_binary_not_fake_switch() -> None:
    """Keep direct-Gauss rollback tied to the prior binary's environment."""
    upgrading = (ROOT / "docs/ops/upgrading.md").read_text(encoding="utf-8")
    config = (ROOT / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )
    combined = upgrading + config
    assert "BI_LEGACY_HTTP" not in combined
    assert "deploy the prior wheel or image" in upgrading
    assert (
        "restore the legacy environment after the prior binary is installed"
        in upgrading
    )


def test_release_docs_pin_the_final_013_boundary() -> None:
    """Keep release notes and rollout docs tied to the final Bot snapshot."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    upgrading = (ROOT / "docs/ops/upgrading.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/guides/deployment.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contract = (ROOT / "docs/contracts/deep-genome/README.md").read_text(
        encoding="utf-8"
    )

    assert "## [0.1.3] — 2026-07-17" in changelog
    assert "Full commit range: `1f8f628..4bc66a3`." in changelog
    assert "Full commit range: `adaa874..1f8f628`." in changelog
    assert "Bot-side implementation" in changelog
    assert "/openapi.json" in upgrading
    assert "/v1/agents" in upgrading
    assert "checkpoints.db" in upgrading
    assert "capabilities" in upgrading
    assert "../ops/upgrading.md" in deployment
    assert "Web/Go" in readme
    assert "docs/handoffs/evidence/README.md" not in readme
    assert "report_revision" in contract
    assert "synthetic shape goldens" in contract


def test_gauss_docs_require_all_three_read_only_layers() -> None:
    """Keep Gauss controls and external operator evidence explicit."""
    runbook = (ROOT / "docs/ops/http-api-runbook.md").read_text(
        encoding="utf-8"
    )
    configuration = (ROOT / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "parse-based validation",
        "transaction(readonly=True)",
        "read-only deployment role",
        "RESET ALL",
        "does not use UNLISTEN",
    ):
        assert phrase in runbook
    assert "External" in runbook
    assert "Pending until the authorized probe" in runbook
    for name in ("TIMEOUT", "MAX_POLL", "ANALYSIS_JOB_TIMEOUT"):
        assert f"`{name}`" in configuration


def test_streaming_docs_use_the_narrowed_contract() -> None:
    """Keep streaming docs aligned with the currently emitted event set."""
    http = (ROOT / "docs/reference/http-api.md").read_text(encoding="utf-8")
    a2ui = (ROOT / "docs/contracts/a2ui/README.md").read_text(encoding="utf-8")
    assert "ToolCallStart" not in http
    assert "ReasoningStart" not in http
    assert "Review confirm, form, and choice" in a2ui
    assert "opened-stream failure projection remains pending" not in http
    assert "exactly one `RunError`" in http


def test_stream_docs_describe_preopen_and_opened_failure_boundaries() -> None:
    """Document the typed lifecycle boundary exercised by HTTP streams."""
    http = (ROOT / "docs/reference/http-api.md").read_text(encoding="utf-8")
    for phrase in (
        "first event is primed",
        "ordinary JSON error",
        "exactly one `RunError`",
        "does not emit `RunFinished`",
        "client cancellation",
    ):
        assert phrase in http


def test_agent_card_interface_matches_enabled_http_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The advertised Agent Card URL has a mounted, real A2A endpoint."""
    monkeypatch.setenv("PHYTOMNI_A2A_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://public.example/base"
    )

    app = create_app()
    route_paths = {getattr(route, "path", "") for route in app.routes}
    assert {
        "/.well-known/agent-card.json",
        "/a2a",
    }.issubset(route_paths)

    config = ApiConfig()
    assert config.A2A_PUBLIC_BASE_URL is not None
    card = build_agent_card(config.A2A_PUBLIC_BASE_URL)
    assert len(card.supported_interfaces) == 1
    interface_url = card.supported_interfaces[0].url

    base = urlsplit(config.A2A_PUBLIC_BASE_URL)
    advertised = urlsplit(interface_url)
    assert advertised.scheme == base.scheme
    assert advertised.netloc == base.netloc
    assert advertised.path == f"{base.path.rstrip('/')}/a2a"


def test_configuration_docs_cover_bounded_api_limits() -> None:
    """Every C6.4 limit has a documented canonical and prefixed alias."""
    configuration = (ROOT / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )
    names = (
        "MEMORY_MAX_ITEMS",
        "MEMORY_MAX_CONTENT_BYTES",
        "MEMORY_MAX_TOTAL_BYTES",
        "MEMORY_MAX_RETRIEVAL",
        "MEMORY_GRAPH_MAX_BYTES",
        "INTEROP_MAX_TARGETS",
        "INTEROP_CACHE_MAX_ENTRIES",
        "A2A_MAX_HISTORY_MESSAGES",
        "A2A_MAX_ARTIFACT_BYTES",
    )

    for name in names:
        assert f"`{name}`" in configuration
        assert f"`PHYTOMNI_{name}`" in configuration


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
    assert _capability_status(
        readme,
        "Calls to external MCP tools or A2A agents",
        "Explicit opt-in from Research/Design",
    )
    assert _capability_status(
        readme,
        "User-scoped memory CRUD API",
        "Opt-in; bounded read-only recall",
    )
    assert "PHYTOMNI_INTEROP_ENABLED" in readme
    assert "/v1/interop/capabilities" in readme
    assert _capability_status(
        readme, "A2A Agent Card and `/a2a` server", "Opt-in core"
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
