# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Execution supervisor provider-join fencing and settlement tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.execution_supervisor_v2 import (
    ready_result_delivery,
    seed_running_remote_execution,
    set_provider_join_lease,
)

from mcp_server_phytomni.runtime import execution_supervisor_service_v2
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent_operation,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_provider_join_losing_lease_after_claim_cannot_publish(
    tmp_path: Path,
) -> None:
    """A claimed old operation is fenced before result/event publication."""

    db_path = str(tmp_path / "provider-join-post-claim-fencing.db")

    baseline = seed_running_remote_execution(
        db_path,
        execution_id="turn-post-claim",
        agent_slug="network",
        arguments={"query": "network"},
    )
    set_provider_join_lease(
        db_path,
        execution_id="turn-post-claim",
        lease_token="lease-old",
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def stale_result():
        started.set()
        await release.wait()
        return {
            "status": "succeeded",
            "result": {"formatted": {"answer": "must not publish"}},
        }

    async def exercise() -> None:
        old = asyncio.create_task(
            invoke_public_agent_operation(
                db_path=db_path,
                owner="alice",
                execution_id="turn-post-claim",
                agent_slug="network",
                operation="reconcile",
                action_id=f"provider-join:{baseline.supervisor_revision}",
                expected_revision=baseline.supervisor_revision,
                arguments={"source": "provider_join"},
                transport="supervisor",
                call=stale_result,
                expected_provider_join_lease_token="lease-old",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        set_provider_join_lease(
            db_path,
            execution_id="turn-post-claim",
            lease_token="lease-new",
        )
        release.set()
        with pytest.raises(
            ExecutionReservationConflictError,
            match="provider_join_lease_lost",
        ):
            await old

    asyncio.run(exercise())
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-post-claim", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None
    assert projection.results == ()


@pytest.mark.parametrize("publication", ["message", "target"])
def test_provider_join_takeover_after_preflight_cannot_publish(
    tmp_path: Path,
    monkeypatch,
    publication: str,
) -> None:
    """The journal and target writes are fenced, not only their preflight."""

    db_path = str(tmp_path / "provider-join-publication-fencing.db")

    baseline = seed_running_remote_execution(
        db_path,
        execution_id="turn-publication-fenced",
        agent_slug="network",
        arguments={"query": "network"},
    )
    journal = SQLiteExecutionJournal(db_path)
    before = journal.get_projection("turn-publication-fenced", owner="alice")
    set_provider_join_lease(
        db_path,
        execution_id="turn-publication-fenced",
        lease_token="lease-old",
    )

    original_require = (
        SQLiteExecutionReservationRepository.require_provider_join_lease
    )
    takeover_done = False

    def takeover_after_successful_preflight(
        self,
        *,
        owner: str,
        execution_id: str,
    ) -> None:
        nonlocal takeover_done
        original_require(self, owner=owner, execution_id=execution_id)
        if takeover_done:
            return
        takeover_done = True
        set_provider_join_lease(
            db_path,
            execution_id=execution_id,
            lease_token="lease-new",
            owner=owner,
        )

    monkeypatch.setattr(
        SQLiteExecutionReservationRepository,
        "require_provider_join_lease",
        takeover_after_successful_preflight,
    )

    async def stale_result():
        formatted = (
            {"formatted": {"answer": "must not publish"}}
            if publication == "message"
            else {}
        )
        execution = (
            {
                "execution": {
                    "artifacts": [
                        {
                            "role": "scientific_report",
                            "name": "stale.pdf",
                            "media_type": "application/pdf",
                            "size_bytes": 42,
                            "download_ref": "obs://private/stale.pdf",
                        }
                    ]
                }
            }
            if publication == "target"
            else {}
        )
        return {
            "status": "succeeded",
            "result": {**formatted, **execution},
        }

    with pytest.raises(
        ExecutionReservationConflictError,
        match="provider_join_lease_lost",
    ):
        asyncio.run(
            invoke_public_agent_operation(
                db_path=db_path,
                owner="alice",
                execution_id="turn-publication-fenced",
                agent_slug="network",
                operation="reconcile",
                action_id=f"provider-join:{baseline.supervisor_revision}",
                expected_revision=baseline.supervisor_revision,
                arguments={"source": "provider_join"},
                transport="supervisor",
                call=stale_result,
                expected_provider_join_lease_token="lease-old",
            )
        )

    after = journal.get_projection("turn-publication-fenced", owner="alice")
    assert after.latest_seq == before.latest_seq
    assert after.status.value == "running"
    assert after.terminal is None
    assert after.results == ()
    with sqlite_transaction(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_target_bindings_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("alice", "turn-publication-fenced"),
        ).fetchone() == (0,)


def test_provider_join_reconcile_failure_keeps_runtime_recoverable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Transient domain aggregation must fail before claiming Runtime."""

    db_path = str(tmp_path / "provider-join-failure.db")

    seed_running_remote_execution(
        db_path,
        execution_id="turn-provider-join-failure",
        agent_slug="network",
        arguments={"to_id": "TO:0000011", "species_code": "osa"},
    )

    class FailingRegistry:
        """Registry raising during provider-join reconciliation."""

        def get_run(self, _run_id: str, *, owner: str):
            """Return run from the test double."""
            assert owner == "alice"
            return SimpleNamespace(status="running", result=None)

        async def reconcile(self, _run_id: str, *, owner: str):
            """Reconcile the configured provider state."""
            assert owner == "alice"
            raise RuntimeError("temporary artifact listing failure")

    monkeypatch.setattr(
        execution_supervisor_service_v2,
        "RunRegistry",
        lambda _db_path: FailingRegistry(),
    )

    with pytest.raises(RuntimeError, match="temporary artifact"):
        asyncio.run(
            execution_supervisor_service_v2.settle_ready_provider_execution(
                db_path,
                "alice",
                "turn-provider-join-failure",
            )
        )

    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-provider-join-failure", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None


def test_provider_join_projects_terminal_result_and_download_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A settled domain run closes Runtime and publishes typed results."""

    db_path = str(tmp_path / "provider-join-success.db")

    seed_running_remote_execution(
        db_path,
        execution_id="turn-provider-join-success",
        agent_slug="network",
        arguments={"to_id": "TO:0000011", "species_code": "osa"},
    )
    delivery_ref = "obs://phytomni/runs/network/report.pdf"
    result = {
        "formatted": {"answer": "Network analysis complete."},
        "execution": {
            "artifacts": [
                {
                    "role": "scientific_report",
                    "name": "report.pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 42,
                    "download_ref": delivery_ref,
                }
            ]
        },
    }

    class TerminalRegistry:
        """Registry projecting a terminal result and download target."""

        def get_run(self, _run_id: str, *, owner: str):
            """Return run from the test double."""
            assert owner == "alice"
            return SimpleNamespace(status="succeeded", result=result)

        async def reconcile(self, _run_id: str, *, owner: str):
            """Reconcile the configured provider state."""
            raise AssertionError(f"terminal run must not be repolled: {owner}")

    monkeypatch.setattr(
        execution_supervisor_service_v2,
        "RunRegistry",
        lambda _db_path: TerminalRegistry(),
    )

    asyncio.run(
        execution_supervisor_service_v2.settle_ready_provider_execution(
            db_path,
            "alice",
            "turn-provider-join-success",
        )
    )

    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-provider-join-success", owner="alice"
    )
    assert projection.status.value == "succeeded"
    assert projection.terminal is not None
    assert len(projection.results) == 1
    with sqlite_transaction(db_path) as connection:
        binding = connection.execute(
            "SELECT role, name, delivery_ref "
            "FROM execution_target_bindings_v2 WHERE owner_ref = ? "
            "AND execution_id = ?",
            ("alice", "turn-provider-join-success"),
        ).fetchone()
    assert binding == ("scientific_report", "report.pdf", delivery_ref)


def test_provider_join_projects_domain_terminal_row_before_runtime_settlement(
    tmp_path: Path,
) -> None:
    """A domain terminal row must not block the one canonical Runtime join."""

    db_path = str(tmp_path / "provider-join-domain-terminal.db")

    seed_running_remote_execution(
        db_path,
        execution_id="turn-provider-join-domain-terminal",
        agent_slug="network",
        arguments={"to_id": "TO:0000011", "species_code": "osa"},
    )
    digest = "sha256:" + "7" * 64
    run_root = "obs://phytomni/runs/network-domain-terminal"
    archive = {
        "role": "result_archive",
        "name": "network-results.zip",
        "media_type": "application/zip",
        "size_bytes": 1_024,
        "downloadable": True,
        "report_context_eligible": False,
        "download_ref": f"result-archive:{digest}",
    }
    terminal_result = {
        "formatted": {"answer": "Network analysis complete."},
        "execution": {
            "artifacts": [],
            "delivery": ready_result_delivery(digest, archive),
        },
        "delivery_internal": {
            "inventory_ref": (
                f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
                ".phytomni-result-inventory.json"
            ),
            "attempts_claimed": 1,
            "last_error_code": None,
        },
    }
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET status = 'succeeded', result_json = ? "
            "WHERE user_id = ? AND execution_id = ?",
            (
                json.dumps(terminal_result),
                "alice",
                "turn-provider-join-domain-terminal",
            ),
        )
        connection.commit()

    asyncio.run(
        execution_supervisor_service_v2.settle_ready_provider_execution(
            db_path,
            "alice",
            "turn-provider-join-domain-terminal",
        )
    )

    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-provider-join-domain-terminal", owner="alice"
    )
    assert projection.status.value == "succeeded"
    assert projection.terminal is not None
    assert [result.name for result in projection.results] == [
        "network-results.zip"
    ]
    with sqlite_transaction(db_path) as connection:
        binding = connection.execute(
            "SELECT target_kind, role, name, delivery_ref "
            "FROM execution_target_bindings_v2 WHERE owner_ref = ? "
            "AND execution_id = ?",
            ("alice", "turn-provider-join-domain-terminal"),
        ).fetchone()
    assert binding == (
        "download",
        "result_archive",
        "network-results.zip",
        f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
        "network-results.zip",
    )
