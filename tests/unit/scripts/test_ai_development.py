# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the read-only AI development helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts/ai_development.py"
    spec = importlib.util.spec_from_file_location("ai_development", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_contains_agents_graphs_and_authorities() -> None:
    payload = _module().architecture_inventory()
    assert len(payload["public_agents"]) == 10
    assert payload["graphs"]["environment"]["classification"] == "internal"
    assert payload["authorities"]["agent_runtime"] == "Phytomni-Bot"


def test_verification_plan_maps_cross_repo_paths() -> None:
    plan = _module().verification_plan(
        [
            "src/mcp_server_phytomni/graphs/manifest.py",
            "../Phytomni-Web/apps/server/external/bot/agent_map.go",
            "../Phytomni-Web/apps/web/src/views/chat/Chat.vue",
        ]
    )
    groups = {item["group"] for item in plan["recipes"]}
    assert {
        "bot_graph",
        "web_go",
        "web_frontend",
        "cross_repo_contract",
    } <= groups


def test_evidence_summary_never_infers_external_activation() -> None:
    payload = _module().evidence_summary(
        [{"command": "pytest -q", "exit_code": 0, "scope": "focused"}]
    )
    assert payload["local_results"][0]["outcome"] == "passed"
    assert payload["external_ci"] == "not_verified"
    assert payload["staging"] == "not_verified"
    assert payload["production"] == "not_verified"
    json.dumps(payload)


def test_current_execution_convergence_has_no_unaccounted_bypass() -> None:
    helper = _module()
    assert helper.execution_convergence_violations() == []


def test_canonical_runtime_has_no_legacy_activation_or_background_map() -> (
    None
):
    root = Path(__file__).resolve().parents[3]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/mcp_server_phytomni").rglob("*.py")
    )
    assert "EXECUTION_RUNTIME_V2_ENABLED" not in sources
    assert "EXECUTION_SUPERVISOR_V2_ENABLED" not in sources
    assert "BACKGROUND_SUBMISSION_AGENT_SLUGS" not in sources
    assert "background_submission_agent_slugs" not in sources


def test_execution_convergence_reports_a_new_direct_agent_task(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/agents/new_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import asyncio\nasyncio.create_task(run())\n", encoding="utf-8"
    )
    violations = helper.execution_convergence_violations(tmp_path)

    assert violations == [
        "durable_create_task_paths: "
        "src/mcp_server_phytomni/agents/new_agent.py"
    ]


def test_execution_convergence_reports_direct_agent_lifecycle_logging(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/agents/new_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("emit_execution_event(event_intent)\n", encoding="utf-8")
    violations = helper.execution_convergence_violations(tmp_path)

    assert violations == [
        "direct_lifecycle_event_paths: "
        "src/mcp_server_phytomni/agents/new_agent.py"
    ]


def test_execution_convergence_rejects_terminal_journal_writer(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = (
        tmp_path / "src/mcp_server_phytomni/runtime/rogue_terminal_writer.py"
    )
    source.parent.mkdir(parents=True)
    source.write_text(
        "event_type = ExecutionEventType.EXECUTION_FAILED\n"
        "journal.append(execution_id, owner=owner, intent=intent)\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "terminal_event_writer_paths: "
        "src/mcp_server_phytomni/runtime/rogue_terminal_writer.py"
    ]


def test_execution_convergence_rejects_legacy_turn_id_mint(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/api/legacy.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'execution_id = IdFactory().new_id("turn")\n', encoding="utf-8"
    )
    canonical = (
        tmp_path / "src/mcp_server_phytomni/runtime/execution_identity_v2.py"
    )
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text('value = f"turn-{uuid4()}"\n', encoding="utf-8")

    assert helper.execution_convergence_violations(tmp_path) == [
        "legacy_execution_identity_paths: "
        "src/mcp_server_phytomni/api/legacy.py"
    ]


def test_direct_graph_execution_is_always_rejected(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/mcp/app.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "async def run(app):\n"
        "    async for item in app.astream({}):\n"
        "        yield item\n",
        encoding="utf-8",
    )
    assert helper.execution_convergence_violations(tmp_path) == [
        "direct_graph_invocation_paths: src/mcp_server_phytomni/mcp/app.py"
    ]


def test_docstrings_and_remote_mcp_tools_are_not_graph_bypasses(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/interop/mcp_client.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '"""The old graph.app.ainvoke({}) path is documented here."""\n'
        "async def call(tool):\n"
        "    return await tool.ainvoke({})\n",
        encoding="utf-8",
    )

    observed = helper.execution_convergence_inventory(tmp_path)

    assert observed["direct_graph_invocation_paths"] == []


def test_detached_public_agent_work_is_always_rejected(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = (
        tmp_path / "src/mcp_server_phytomni/runtime/background_submission.py"
    )
    source.parent.mkdir(parents=True)
    source.write_text(
        "import asyncio\ndef launch(coro):\n    asyncio.create_task(coro)\n",
        encoding="utf-8",
    )
    assert helper.execution_convergence_violations(tmp_path) == [
        "durable_create_task_paths: "
        "src/mcp_server_phytomni/runtime/background_submission.py",
        "retired_execution_module_paths: "
        "src/mcp_server_phytomni/runtime/background_submission.py",
    ]


def test_legacy_sync_run_writer_is_always_rejected(tmp_path: Path) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/api/routes/agents.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def dispatch(run_lifecycle):\n"
        "    return run_lifecycle.reserve_sync_run(agent='chat')\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "legacy_sync_run_writer_paths: "
        "src/mcp_server_phytomni/api/routes/agents.py"
    ]


def test_legacy_stream_run_writer_is_always_rejected(tmp_path: Path) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/api/streaming.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def project(persistence, run_id, owner, result):\n"
        "    persistence.create_running_stream_run(run_id, 'chat', owner, {})\n"
        "    persistence.update_running_stream_result(run_id, owner, result)\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "legacy_stream_run_writer_paths: "
        "src/mcp_server_phytomni/api/streaming.py"
    ]


def test_secondary_public_runtime_writer_is_always_rejected(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = tmp_path / "src/mcp_server_phytomni/api/a2a/runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def resume(registry, spec):\n"
        "    registry.create_run(spec)\n"
        "    registry.settle_run(spec)\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "secondary_runtime_writer_paths: "
        "src/mcp_server_phytomni/api/a2a/runtime.py"
    ]


def test_retired_a2ui_persistence_module_cannot_return(
    tmp_path: Path,
) -> None:
    helper = _module()
    source = (
        tmp_path / "src/mcp_server_phytomni/api/a2ui_review_persistence.py"
    )
    source.parent.mkdir(parents=True)
    source.write_text("# second lifecycle authority\n", encoding="utf-8")

    assert helper.execution_convergence_violations(tmp_path) == [
        "retired_execution_module_paths: "
        "src/mcp_server_phytomni/api/a2ui_review_persistence.py"
    ]


def test_lease_heartbeats_and_file_io_threads_are_not_execution_bypasses(
    tmp_path: Path,
) -> None:
    helper = _module()
    recovery = tmp_path / "src/mcp_server_phytomni/agents/research/recovery.py"
    report = tmp_path / "src/mcp_server_phytomni/agents/deep_genome/report.py"
    recovery.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    recovery.write_text(
        "import asyncio\n"
        "def tick(self):\n"
        "    asyncio.create_task(self._heartbeat_loop())\n",
        encoding="utf-8",
    )
    report.write_text(
        "import threading\n"
        "def write():\n"
        "    def _run(): pass\n"
        "    threading.Thread(target=_run).start()\n",
        encoding="utf-8",
    )

    observed = helper.execution_convergence_inventory(tmp_path)

    assert observed["durable_create_task_paths"] == []


def test_advertised_todo_cannot_exist_only_as_an_unreachable_helper(
    tmp_path: Path,
) -> None:
    helper = _module()
    runtime = (
        tmp_path / "src/mcp_server_phytomni/runtime/execution_runtime_v2.py"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        "class ExecutionRuntime:\n    pass\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "unreachable_public_fact_producers: todo.snapshot",
        "unreachable_public_fact_producers: public_summary",
    ]


def test_advertised_public_summary_requires_a_business_call_site(
    tmp_path: Path,
) -> None:
    helper = _module()
    runtime = (
        tmp_path / "src/mcp_server_phytomni/runtime/execution_runtime_v2.py"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text('TODO = "todo.snapshot"\n', encoding="utf-8")
    public_trace = (
        tmp_path / "src/mcp_server_phytomni/agents/network/public_trace.py"
    )
    public_trace.parent.mkdir(parents=True)
    public_trace.write_text(
        "def publish_summary():\n    emit_reasoning_summary('safe')\n",
        encoding="utf-8",
    )

    assert helper.execution_convergence_violations(tmp_path) == [
        "unreachable_public_fact_producers: public_summary"
    ]

    (public_trace.parent / "agent.py").write_text(
        "def run():\n    publish_summary()\n",
        encoding="utf-8",
    )
    assert helper.execution_convergence_violations(tmp_path) == []
