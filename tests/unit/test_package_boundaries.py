# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for package boundary conventions after module reorganization."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE_NAME = "mcp_server_phytomni"
ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src"
PACKAGE_DIR = SOURCE_ROOT / PACKAGE_NAME
LEGACY_ROOT_MODULES = frozenset(
    {
        "agent_option_helpers",
        "agent_registry",
        "analysis_workflow_helpers",
        "analyst_agents",
        "analyst_graph_nodes",
        "analyst_storage",
        "brief_gene_agents",
        "chat_agents",
        "data_agents",
        "deep_genome_agents",
        "deep_genome_dispatch",
        "deep_genome_formatting",
        "deep_genome_profile",
        "deep_genome_report",
        "deep_genome_summary",
        "digital_design_agents",
        "environment_agents",
        "evolution_agents",
        "gene_network_agents",
        "in_silico_research_agents",
        "knowledge_agents",
        "knowledge_retrieval",
        "langgraph_runner",
        "obs_storage",
        "path_policy",
        "review_agents",
        "task_manager",
        "tool_handlers",
        "utils",
        "workflow_mixins",
    }
)


def _module_name_for_path(path: Path) -> str:
    """Return the importable module name for a source path."""
    relative = path.relative_to(SOURCE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _current_package_for_path(path: Path) -> str:
    """Return the package used as the anchor for relative imports."""
    module_name = _module_name_for_path(path)
    if path.name == "__init__.py":
        return module_name
    return module_name.rsplit(".", maxsplit=1)[0]


def _resolve_relative_import(path: Path, node: ast.ImportFrom) -> str:
    """Resolve a relative import to its absolute module prefix."""
    package_parts = _current_package_for_path(path).split(".")
    prefix_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        prefix_parts.extend(node.module.split("."))
    return ".".join(prefix_parts)


def _legacy_root_module(module_name: str) -> str | None:
    """Return the legacy root module component when one is referenced."""
    if module_name == PACKAGE_NAME:
        return None
    prefix = f"{PACKAGE_NAME}."
    if not module_name.startswith(prefix):
        return None
    root_module = module_name.removeprefix(prefix).split(".", maxsplit=1)[0]
    if root_module in LEGACY_ROOT_MODULES:
        return root_module
    return None


def _import_violations(path: Path) -> list[str]:
    """Return legacy root module imports found in a source file."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    violations = []

    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = _legacy_root_module(alias.name)
                if root_module:
                    violations.append(f"{node.lineno}: {alias.name}")
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        module_name = node.module or ""
        if node.level:
            module_name = _resolve_relative_import(path, node)
        root_module = _legacy_root_module(module_name)
        if root_module:
            violations.append(f"{node.lineno}: {module_name}")
        if module_name == PACKAGE_NAME:
            for alias in node.names:
                if alias.name in LEGACY_ROOT_MODULES:
                    violations.append(
                        f"{node.lineno}: {module_name}.{alias.name}"
                    )

    return violations


def test_legacy_root_module_files_stay_removed():
    """Verify old root module files are not reintroduced."""
    present_modules = [
        module_name
        for module_name in sorted(LEGACY_ROOT_MODULES)
        if (PACKAGE_DIR / f"{module_name}.py").exists()
    ]

    assert present_modules == []


def test_production_imports_do_not_use_legacy_root_modules():
    """Verify production code imports from the new package boundaries."""
    violations = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative_path = path.relative_to(ROOT).as_posix()
        for import_detail in _import_violations(path):
            violations.append(f"{relative_path}:{import_detail}")

    assert not violations
