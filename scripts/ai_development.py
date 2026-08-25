#!/usr/bin/env python3
"""Read-only architecture, verification-plan, and evidence helpers for AI."""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp_server_phytomni.public_agent_catalog import (
    PUBLIC_AGENT_CATALOG,
    export_public_agent_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "src/mcp_server_phytomni/graphs/manifests"


def _has_direct_graph_call(source: str) -> bool:
    """Detect executable graph calls while ignoring docs and MCP tools."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(
            node.func, ast.Attribute
        ):
            continue
        if node.func.attr == "astream":
            return True
        if node.func.attr != "ainvoke":
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "tool":
            continue
        return True
    return False


def _call_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


_TERMINAL_EVENT_MEMBERS = {
    "EXECUTION_SUCCEEDED",
    "EXECUTION_PARTIAL",
    "EXECUTION_FAILED",
    "EXECUTION_CANCELLED",
    "EXECUTION_TIMED_OUT",
}
_TERMINAL_EVENT_VALUES = {
    "execution.succeeded",
    "execution.partial",
    "execution.failed",
    "execution.cancelled",
    "execution.timed_out",
}


def _has_terminal_journal_append(source: str) -> bool:
    """Detect direct terminal publication through a journal receiver."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    terminal_reference = any(
        (
            isinstance(node, ast.Attribute)
            and node.attr in _TERMINAL_EVENT_MEMBERS
        )
        or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _TERMINAL_EVENT_VALUES
        )
        for node in ast.walk(tree)
    )
    if not terminal_reference:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(
            node.func, ast.Attribute
        ):
            continue
        if node.func.attr not in {"append", "_append_locked"}:
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and "journal" in receiver.id.lower():
            return True
        if (
            isinstance(receiver, ast.Attribute)
            and "journal" in receiver.attr.lower()
        ):
            return True
        if (
            isinstance(receiver, ast.Call)
            and "journal" in _call_name(receiver.func).lower()
        ):
            return True
    return False


def _has_durable_task_call(source: str) -> bool:
    """Find detached work, excluding bounded heartbeat and file-I/O helpers."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(
            node.func, ast.Attribute
        ):
            continue
        owner = node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else ""
        if owner_name == "asyncio" and node.func.attr in {
            "create_task",
            "ensure_future",
        }:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Call) and _call_name(first.func) == (
                "_heartbeat_loop"
            ):
                continue
            return True
        if owner_name == "threading" and node.func.attr == "Thread":
            target = next(
                (item.value for item in node.keywords if item.arg == "target"),
                None,
            )
            if _call_name(target) == "_run":
                continue
            return True
    return False


def _public_summary_producer_names(source: str) -> set[str]:
    """Return finite public-summary wrappers defined by one module."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    emitter_names = {"emit_reasoning_summary", "emit_decision_note"}
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(child, ast.Call)
            and _call_name(child.func) in emitter_names
            for child in ast.walk(node)
        )
    }


def _agent_has_reachable_public_summary(root: Path, slug: str) -> bool:
    """Require an advertised summary producer to have a business call site."""
    agent_root = root / "src/mcp_server_phytomni/agents" / slug
    public_trace = agent_root / "public_trace.py"
    if not public_trace.exists():
        return False
    producer_names = _public_summary_producer_names(
        public_trace.read_text(encoding="utf-8")
    )
    if not producer_names:
        return False
    emitter_names = {"emit_reasoning_summary", "emit_decision_note"}
    for path in agent_root.rglob("*.py"):
        if path == public_trace:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if any(
            isinstance(node, ast.Call)
            and _call_name(node.func) in producer_names | emitter_names
            for node in ast.walk(tree)
        ):
            return True
    return False


def _has_legacy_sync_run_writer(source: str) -> bool:
    """Detect the superseded V1 request-owned run persistence path."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    legacy_calls = {
        "record_sync_run",
        "_record_sync_run",
        "reserve_sync_run",
        "settle_reserved_sync_run",
        "fail_reserved_sync_run",
    }
    return any(
        isinstance(node, ast.Call) and _call_name(node.func) in legacy_calls
        for node in ast.walk(tree)
    )


def _has_legacy_stream_run_writer(source: str) -> bool:
    """Detect transport-owned V1 stream lifecycle/result persistence."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    legacy_calls = {
        "create_running_stream_run",
        "update_running_stream_result",
    }
    return any(
        isinstance(node, ast.Call) and _call_name(node.func) in legacy_calls
        for node in ast.walk(tree)
    )


def _has_secondary_runtime_writer(source: str) -> bool:
    """Detect a public adapter reviving a second lifecycle authority."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    forbidden_calls = {
        "create_run",
        "settle_run",
        "claim_a2ui_action",
        "complete_a2ui_action",
        "emit_input_required",
        "emit_input_resolved",
    }
    return any(
        isinstance(node, ast.Call) and _call_name(node.func) in forbidden_calls
        for node in ast.walk(tree)
    )


_HARDCODED_BACKGROUND_POLICY = re.compile(
    r"BACKGROUND_SUBMISSION_AGENT_SLUGS\s*:\s*[^=]+="
)
_DIRECT_LIFECYCLE_EVENT = re.compile(
    r"(?:emit_(?:run|input|remote)_[A-Za-z0-9_]+|"
    r"emit_execution_event|SQLiteExecutionJournal|\.journal\.append)\s*\("
)
_LEGACY_EXECUTION_ID_MINT = re.compile(
    r'IdFactory\(\)\.new_id\(["\']turn["\']\)|'
    r"execution_id\s*=\s*current_request_id\(\)"
)
_PUBLIC_EXECUTION_UUID_MINT = re.compile(r'f["\']turn-\{uuid4\(\)\}["\']')
_CANONICAL_EXECUTION_ID_FACTORY = (
    "src/mcp_server_phytomni/runtime/execution_identity_v2.py"
)
_SECONDARY_RUNTIME_WRITER_OWNERS = {
    "src/mcp_server_phytomni/runtime/execution_runtime_v2.py",
    "src/mcp_server_phytomni/runtime/run_registry.py",
    "src/mcp_server_phytomni/runtime/run_registry_views.py",
}
_RETIRED_EXECUTION_MODULES = {
    "src/mcp_server_phytomni/api/a2ui_review_persistence.py",
    "src/mcp_server_phytomni/runtime/background_submission.py",
}


def architecture_inventory() -> dict[str, Any]:
    """Return public agents, graphs, dependencies, and authorities."""
    graphs: dict[str, Any] = {}
    for path in sorted(MANIFEST_DIR.glob("*.graph.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        graph_id = payload.get("graph_id") or path.name.removesuffix(
            ".graph.json"
        )
        graphs[graph_id] = {
            key: payload.get(key)
            for key in (
                "classification",
                "public_agent",
                "lifecycle",
                "subgraph_dependencies",
                "remote_providers",
            )
        }
    return {
        "schema_version": 1,
        "public_agents": export_public_agent_catalog()["agents"],
        "graphs": graphs,
        "authorities": {
            "agent_runtime": "Phytomni-Bot",
            "authentication_permissions_visible_history": "Phytomni-Web",
            "cross_repository_contract": "Bot-first compatible deployment",
        },
    }


def execution_convergence_inventory(root: Path = ROOT) -> dict[str, Any]:
    """Return execution responsibilities outside shared runtime owners."""

    source_root = root / "src/mcp_server_phytomni"
    graph_calls: set[str] = set()
    durable_task_calls: set[str] = set()
    hardcoded_policies: set[str] = set()
    direct_lifecycle_events: set[str] = set()
    legacy_execution_id_mints: set[str] = set()
    legacy_sync_run_writers: set[str] = set()
    legacy_stream_run_writers: set[str] = set()
    secondary_runtime_writers: set[str] = set()
    terminal_event_writers: set[str] = set()
    retired_execution_modules: set[str] = set()
    public_execution_uuid_mints: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        if (
            _has_direct_graph_call(source)
            and relative
            != "src/mcp_server_phytomni/runtime/langgraph_runner.py"
        ):
            graph_calls.add(relative)
        if _has_durable_task_call(source) and (
            "/agents/" in f"/{relative}"
            or relative.endswith("/runtime/background_submission.py")
        ):
            durable_task_calls.add(relative)
        if _HARDCODED_BACKGROUND_POLICY.search(source):
            hardcoded_policies.add(relative)
        if "/agents/" in f"/{relative}" and _DIRECT_LIFECYCLE_EVENT.search(
            source
        ):
            direct_lifecycle_events.add(relative)
        if _LEGACY_EXECUTION_ID_MINT.search(source):
            legacy_execution_id_mints.add(relative)
        if (
            relative != "src/mcp_server_phytomni/api/run_lifecycle.py"
            and _has_legacy_sync_run_writer(source)
        ):
            legacy_sync_run_writers.add(relative)
        if _has_legacy_stream_run_writer(source):
            legacy_stream_run_writers.add(relative)
        if (
            relative not in _SECONDARY_RUNTIME_WRITER_OWNERS
            and _has_secondary_runtime_writer(source)
        ):
            secondary_runtime_writers.add(relative)
        if (
            relative
            != "src/mcp_server_phytomni/runtime/execution_reservation_v2.py"
            and _has_terminal_journal_append(source)
        ):
            terminal_event_writers.add(relative)
        if relative in _RETIRED_EXECUTION_MODULES:
            retired_execution_modules.add(relative)
        public_execution_uuid_mints.extend(
            relative for _ in _PUBLIC_EXECUTION_UUID_MINT.finditer(source)
        )
    unreachable_public_fact_producers: list[str] = []
    runtime_path = (
        root / "src/mcp_server_phytomni/runtime/execution_runtime_v2.py"
    )
    if runtime_path.exists():
        runtime_source = runtime_path.read_text(encoding="utf-8")
        if any(item.todo_phases for item in PUBLIC_AGENT_CATALOG) and (
            '"todo.snapshot"' not in runtime_source
            and "'todo.snapshot'" not in runtime_source
        ):
            unreachable_public_fact_producers.append("todo.snapshot")
        if any(
            not _agent_has_reachable_public_summary(root, item.slug)
            for item in PUBLIC_AGENT_CATALOG
            if item.public_summary == "explicit"
        ):
            unreachable_public_fact_producers.append("public_summary")
    return {
        "schema_version": 1,
        "direct_graph_invocation_paths": sorted(graph_calls),
        "durable_create_task_paths": sorted(durable_task_calls),
        "hardcoded_runtime_policy_paths": sorted(hardcoded_policies),
        "direct_lifecycle_event_paths": sorted(direct_lifecycle_events),
        "legacy_execution_identity_paths": sorted(legacy_execution_id_mints),
        "legacy_sync_run_writer_paths": sorted(legacy_sync_run_writers),
        "legacy_stream_run_writer_paths": sorted(legacy_stream_run_writers),
        "secondary_runtime_writer_paths": sorted(secondary_runtime_writers),
        "terminal_event_writer_paths": sorted(terminal_event_writers),
        "retired_execution_module_paths": sorted(retired_execution_modules),
        "public_execution_identity_mints": public_execution_uuid_mints,
        "unreachable_public_fact_producers": (
            unreachable_public_fact_producers
        ),
    }


def execution_convergence_violations(
    root: Path = ROOT,
) -> list[str]:
    """Report every execution-runtime bypass; historical allowlists are forbidden."""

    observed = execution_convergence_inventory(root)
    violations: list[str] = []
    for field in (
        "direct_graph_invocation_paths",
        "durable_create_task_paths",
        "hardcoded_runtime_policy_paths",
        "direct_lifecycle_event_paths",
        "legacy_execution_identity_paths",
        "legacy_sync_run_writer_paths",
        "legacy_stream_run_writer_paths",
        "secondary_runtime_writer_paths",
        "terminal_event_writer_paths",
        "retired_execution_module_paths",
        "unreachable_public_fact_producers",
    ):
        for path in observed[field]:
            violations.append(f"{field}: {path}")
    for path in observed["public_execution_identity_mints"]:
        if path != _CANONICAL_EXECUTION_ID_FACTORY:
            violations.append(f"duplicate execution identity mint: {path}")
    canonical_factory = root / _CANONICAL_EXECUTION_ID_FACTORY
    if (
        canonical_factory.exists()
        and observed["public_execution_identity_mints"].count(
            _CANONICAL_EXECUTION_ID_FACTORY
        )
        != 1
    ):
        violations.append("canonical execution identity mint is not unique")
    return violations


_RECIPES = {
    "bot_core": "python -m pytest <focused Bot tests>",
    "bot_graph": (
        "python scripts/visualize_agent_graphs.py --check-manifest "
        "src/mcp_server_phytomni/graphs/manifests"
    ),
    "web_go": "cd apps/server && go test ./...",
    "web_frontend": (
        "cd apps/web && npm run type-check && npm run test:run && npm run build"
    ),
    "cross_repo_contract": (
        "python scripts/check_public_agent_catalog.py && "
        "python scripts/check_bot_web_compatibility.py"
    ),
    "web_visual": "cd apps/web && npm run test:visual",
}


def verification_plan(paths: Sequence[str]) -> dict[str, Any]:
    """Map changed paths to the minimum relevant verification recipes."""
    normalized = tuple(path.replace("\\", "/") for path in paths)
    groups: set[str] = set()
    for path in normalized:
        if "mcp_server_phytomni/graphs/" in path:
            groups.update({"bot_core", "bot_graph"})
        elif "Phytomni-Bot" in path or path.startswith(
            ("src/", "tests/", "scripts/")
        ):
            groups.add("bot_core")
        if "Phytomni-Web/apps/server/" in path:
            groups.add("web_go")
        if "Phytomni-Web/apps/web/" in path:
            groups.add("web_frontend")
            if any(
                token in path for token in ("views/", "components/", ".vue")
            ):
                groups.add("web_visual")
        if "external/bot/" in path or "public-agent-catalog" in path:
            groups.add("cross_repo_contract")
    return {
        "schema_version": 1,
        "changed_paths": list(normalized),
        "recipes": [
            {"group": group, "command": _RECIPES[group]}
            for group in sorted(groups)
        ],
    }


def evidence_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Build bounded local evidence without inferring external state."""
    local = []
    for result in results:
        exit_code = result.get("exit_code")
        local.append(
            {
                "command": str(result.get("command", "")),
                "scope": str(result.get("scope", "unspecified")),
                "exit_code": exit_code,
                "outcome": "passed" if exit_code == 0 else "failed",
            }
        )
    return {
        "schema_version": 1,
        "local_results": local,
        "external_ci": "not_verified",
        "staging": "not_verified",
        "production": "not_verified",
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run one read-only helper subcommand and print deterministic JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    convergence = commands.add_parser("convergence")
    convergence.add_argument("--check", action="store_true")
    plan = commands.add_parser("verify-plan")
    plan.add_argument("paths", nargs="+")
    evidence = commands.add_parser("evidence")
    evidence.add_argument("results", type=Path)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        payload = architecture_inventory()
    elif args.command == "convergence":
        violations = execution_convergence_violations(ROOT)
        payload = execution_convergence_inventory()
        payload["violations"] = violations
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if args.check and violations else 0
    elif args.command == "verify-plan":
        payload = verification_plan(args.paths)
    else:
        payload = evidence_summary(
            json.loads(args.results.read_text(encoding="utf-8"))
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
