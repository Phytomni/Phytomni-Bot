# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""GetRun memory-class hide and Research outbox bind tests."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.api import research_input as research_input_mod
from mcp_server_phytomni.api.research_input import (
    build_research_input_coordinator,
    clear_research_input_runtime,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)
from mcp_server_phytomni.runtime.task_reconcile import (
    bind_research_relaunch_outbox,
    get_research_relaunch_outbox,
    reconcile_task,
)

pytestmark = pytest.mark.unit


@pytest.fixture(name="mgr_path")
def _mgr_path(tmp_path: Path) -> str:
    """Return a fresh tasks DB path under ``tmp_path``."""
    return str(tmp_path / "tasks.db")


@pytest.fixture(autouse=True)
def _reset_research_relaunch_outbox():
    """Drop any GetRun outbox bound by a previous test."""
    yield
    bind_research_relaunch_outbox(None)


def _bind_manager(
    monkeypatch: pytest.MonkeyPatch, mgr_path: str
) -> TaskManager:
    """Point reconcile at a fresh TaskManager for one test."""
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    return TaskManager(mgr_path)


@pytest.mark.asyncio
async def test_reconcile_hides_analyst_memory_class_failure(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analyst memory-class FAILED stays in-progress locally."""
    mgr = _bind_manager(monkeypatch, mgr_path)
    mgr.record(
        Submission(
            task_id="an-oom",
            status="submitted",
            output_dir="/obs/run",
            run_context=RunContext(agent="analyst"),
            input_fingerprint="a" * 64,
        )
    )

    async def _failed(t_id: str, **_: Any) -> dict[str, str]:
        assert t_id == "an-oom"
        return {"status": "FAILED", "message": "MemoryError"}

    async def _oom_log(_t_id: str, **_: Any) -> dict[str, object]:
        return {
            "logs": [{"content": "worker hit OOM"}],
            "text": "OOM",
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _failed,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _oom_log,
    )

    result = await reconcile_task("an-oom")

    assert str(result["status"]).lower() != "failed"
    assert result["status"] == "submitted"
    row = mgr.get_task("an-oom")
    assert row is not None
    assert row["status"] == "submitted"


@pytest.mark.asyncio
async def test_reconcile_persists_analyst_non_memory_failure(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ValueError FAILED analyst row is still persisted failed."""
    mgr = _bind_manager(monkeypatch, mgr_path)
    mgr.record(
        Submission(
            task_id="an-value",
            status="submitted",
            output_dir="/obs/run",
            run_context=RunContext(agent="analyst"),
        )
    )

    async def _failed(_t_id: str, **_: Any) -> dict[str, str]:
        return {
            "status": "FAILED",
            "message": "ValueError missing column",
        }

    async def _empty_log(_t_id: str, **_: Any) -> dict[str, object]:
        return {"logs": []}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _failed,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _empty_log,
    )

    result = await reconcile_task("an-value")

    assert result["status"] == "FAILED"
    row = mgr.get_task("an-value")
    assert row is not None
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_reconcile_persists_network_memory_class_failure(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Network agents keep today's persist-failed path on MemoryError."""
    mgr = _bind_manager(monkeypatch, mgr_path)
    mgr.record(
        Submission(
            task_id="net-oom",
            status="submitted",
            output_dir="/obs/run",
            run_context=RunContext(agent="network"),
        )
    )

    async def _failed(_t_id: str, **_: Any) -> dict[str, str]:
        return {"status": "FAILED", "message": "MemoryError"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _failed,
    )

    result = await reconcile_task("net-oom")

    assert result["status"] == "FAILED"
    row = mgr.get_task("net-oom")
    assert row is not None
    assert row["status"] == "failed"


def _install_research_memory_failure(
    monkeypatch: pytest.MonkeyPatch,
    mgr_path: str,
    *,
    dispatch_id: str = "dispatch-1",
) -> None:
    """Record one Research child and stub a memory-class live FAILED."""
    mgr = _bind_manager(monkeypatch, mgr_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile._research_outbox_lookup",
        lambda _db, _ids: (
            dispatch_id,
            {"compute_resource": "small"},
        ),
    )
    mgr.record(
        Submission(
            task_id="rs-oom",
            status="submitted",
            output_dir="/obs/run",
            run_context=RunContext(agent="research"),
            source_task_id="ei-remote-1",
        )
    )

    async def _failed(t_id: str, **_: Any) -> dict[str, str]:
        assert t_id == "ei-remote-1"
        return {"status": "FAILED", "message": "MemoryError"}

    async def _oom_log(_t_id: str, **_: Any) -> dict[str, object]:
        return {
            "logs": [{"content": "worker hit OOM during merge"}],
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _failed,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _oom_log,
    )


@pytest.mark.asyncio
async def test_reconcile_research_memory_relaunch_uses_bound_outbox(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GetRun relaunch uses the bound production outbox submit port."""
    _install_research_memory_failure(monkeypatch, mgr_path)
    relaunch = AsyncMock()
    bind_research_relaunch_outbox(
        SimpleNamespace(relaunch_memory_exhausted=relaunch)
    )

    result = await reconcile_task("rs-oom")

    assert result["status"] == "submitted"
    relaunch.assert_awaited_once_with(
        "dispatch-1",
        {"status": "FAILED", "message": "MemoryError"},
        {
            "logs": [
                {"content": "worker hit OOM during merge"},
            ]
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("package_propagates", [False, True])
async def test_reconcile_research_memory_relaunch_warns_when_unbound(
    mgr_path: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    package_propagates: bool,
) -> None:
    """Missing bind must not construct a submit-less outbox."""
    monkeypatch.setattr(
        logging.getLogger("mcp_server_phytomni"),
        "propagate",
        package_propagates,
    )
    _install_research_memory_failure(monkeypatch, mgr_path)
    logger = logging.getLogger("mcp_server_phytomni.runtime.task_reconcile")
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    monkeypatch.setattr(logger, "propagate", False)
    caplog.set_level(logging.WARNING, logger=logger.name)

    result = await reconcile_task("rs-oom")

    assert result["status"] == "submitted"
    assert caplog.record_tuples == [
        (
            logger.name,
            logging.WARNING,
            "reconcile: research memory relaunch skipped for dispatch-1; "
            "submit port is unbound",
        )
    ]


def test_build_research_input_coordinator_binds_relaunch_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production coordinator construction wires GetRun's relaunch outbox."""
    outbox = object()
    coordinator = SimpleNamespace(outbox=outbox, recovery=object())

    def _from_production(
        _cls: object, _request: object = None, **_ports: object
    ) -> object:
        return coordinator

    monkeypatch.setattr(
        research_input_mod.ResearchInputCoordinator,
        "from_production",
        classmethod(_from_production),
    )
    monkeypatch.setattr(
        research_input_mod,
        "register_recovery_service",
        lambda _service: None,
    )
    clear_research_input_runtime()

    built = build_research_input_coordinator()

    assert built is coordinator
    assert get_research_relaunch_outbox() is outbox
