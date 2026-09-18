# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable owner-scoped cancellation contracts for Research runs."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.agents.research.dispatch_runtime import (
    build_research_dispatch_runtime,
)
from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInventoryRequest,
    validate_research_inventory,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.agents.research.recovery import (
    ResearchRecoveryService,
)
from mcp_server_phytomni.agents.research.recovery_support import (
    ResearchGrantRevocation,
)
from mcp_server_phytomni.api.relay.research_grants import (
    ResearchGrantResolve,
    ResearchGrantRevoke,
    ResearchGrantStore,
)
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchInputStore,
    cancel_research_run,
)
from mcp_server_phytomni.runtime.research_input_store_support import (
    ResearchCancellationOutcome,
)
from mcp_server_phytomni.runtime.research_input_types import (
    ResearchWorkUnitRecord,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)

pytestmark = pytest.mark.unit


def _seed_research_parent(
    tmp_path: Path,
    run_id: str,
    *,
    owner: str = "u1",
    outbox_state: str | None = None,
) -> tuple[ResearchInputStore, str]:
    """Create one admitted Research parent and an optional child outbox row."""
    db_path = str(tmp_path / f"{run_id}.db")
    RunRegistry(db_path)
    store = ResearchInputStore(db_path)
    reservation = store.reserve_admission(
        run_id=run_id,
        owner=owner,
        identity_digest="a" * 64,
        identity_kind="header",
        header_alias_digest=None,
        client_fingerprint="b" * 64,
        original_query_digest="c" * 64,
        original_query_length=1,
        effective_query="q",
        source_map={},
        candidates=(),
        managed_snapshot=(),
        locale="en-US",
        root_input_digest="c" * 64,
    )
    assert reservation is not None
    if outbox_state is not None:
        now = "2026-08-09T00:00:00+00:00"
        with closed_sqlite_connection(db_path) as connection:
            connection.execute(
                "INSERT INTO research_dispatch_outbox "
                "(outbox_id, run_id, unit_id, state, child_ordinal, "
                "created_at, updated_at, sent_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{run_id}:child:0",
                    run_id,
                    f"{run_id}:child:0",
                    outbox_state,
                    0,
                    now,
                    now,
                    now if outbox_state == "sent" else None,
                ),
            )
    return store, db_path


def _run_row(db_path: str, run_id: str) -> tuple[object, ...]:
    """Read the public cancellation fields without projecting them."""
    with closed_sqlite_connection(db_path) as connection:
        row = connection.execute(
            "SELECT status, stage, revision FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None
    return row


def _race_claimed_callback_with_cancellation(
    store: ResearchInputStore,
    claimed: ResearchWorkUnitRecord,
) -> tuple[ResearchCancellationOutcome, bool]:
    """Run the bounded late-callback boundary on two real SQLite workers."""
    rendezvous = threading.Barrier(2)
    callback_ready = threading.Event()
    cancellation_done = threading.Event()

    def finish_callback() -> bool:
        """Attempt the completion after cancellation has committed."""
        rendezvous.wait()
        callback_ready.set()
        assert cancellation_done.wait(timeout=5)
        return store.complete_work(
            claimed,
            "succeeded",
            datetime(2026, 8, 9, 0, 0, 1, tzinfo=UTC),
            output={"unexpected": "late callback"},
        )

    def cancel_parent() -> ResearchCancellationOutcome:
        """Commit the terminal CAS when the callback reaches its boundary."""
        rendezvous.wait()
        assert callback_ready.wait(timeout=5)
        try:
            return cancel_research_run(store, claimed.run_id, "u1", 0)
        finally:
            cancellation_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        callback = executor.submit(finish_callback)
        cancellation = executor.submit(cancel_parent)
        outcome = cancellation.result(timeout=10)
        completed = callback.result(timeout=10)
    return outcome, completed


def _seed_pending_grant_outbox(
    db_path: str,
    run_id: str,
    grant_ids: tuple[str, ...],
    *,
    binding: tuple[str, str] | None = None,
    state: str = "pending",
) -> None:
    """Create a pending Bot outbox row with private authority IDs."""
    now = "2026-08-09T00:00:00+00:00"
    authority_parent, fingerprint = binding or (run_id, "fingerprint-1")
    encoded_binding = {
        "parent_run_id": authority_parent,
        "execution_fingerprint": fingerprint,
    }
    projection = json.dumps(
        {
            "authority_ids": list(grant_ids),
            "execution_fingerprint": fingerprint,
            "research_grant_binding": encoded_binding,
        }
    )
    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE research_input_resolutions SET "
            "execution_fingerprint = ?, final_projection_json = ? "
            "WHERE run_id = ?",
            (fingerprint, projection, run_id),
        )
        connection.execute(
            "INSERT INTO research_work_units "
            "(unit_id, run_id, kind, state) VALUES "
            "(?, ?, 'dispatch', 'pending')",
            (f"{run_id}:dispatch:0", run_id),
        )
        connection.execute(
            "INSERT INTO research_dispatch_outbox "
            "(outbox_id, run_id, unit_id, state, child_ordinal, "
            "dispatch_fingerprint, payload_json, grant_ids_json, "
            "created_at, updated_at) VALUES "
            "(?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
            (
                f"{run_id}:dispatch:0",
                run_id,
                f"{run_id}:dispatch:0",
                state,
                f"{run_id}:child-fingerprint",
                json.dumps({"research_grant_binding": encoded_binding}),
                json.dumps(list(grant_ids)),
                now,
                now,
            ),
        )


def _seed_authority_grant(
    db_path: str, run_id: str, fingerprint: str
) -> tuple[ResearchGrantStore, str]:
    """Create one active grant in the independent authority database."""
    store = ResearchGrantStore(db_path)
    snapshot = ResearchObjectSnapshot(
        dataset_id="dataset-001",
        size_bytes=1,
        etag="etag-001",
        version_id="version-001",
        last_modified="2026-08-09T00:00:00+00:00",
        placeholder=False,
        snapshot_digest="snapshot-001",
    )
    grant = store.resolve_or_replay(
        ResearchGrantResolve(
            principal_key_prefix="ptm_test",
            parent_run_id=run_id,
            execution_fingerprint=fingerprint,
            objects=(
                ResearchObjectCandidate(
                    dataset_id="dataset-001",
                    exact_reference="obs://private-bucket/data.tsv",
                    compound_suffix=".tsv",
                ),
            ),
            authorities=(
                ResearchObjectAuthority(
                    dataset_id="dataset-001",
                    authority_id="source-authority-001",
                    snapshot=snapshot,
                ),
            ),
        ),
        datetime(2026, 8, 9, tzinfo=UTC),
    )[0]
    return store, grant.grant_id


class _ProvisionalScopeMetadataPort:
    """Track provisional authority scope and enforce exact revocation."""

    def __init__(self) -> None:
        self.active: dict[str, tuple[str, str]] = {}
        self.resolve_requests: list[ResearchObjectResolveRequest] = []
        self.revoke_requests: list[ResearchObjectRevokeRequest] = []

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Issue one scoped authority per exact dataset reference."""
        self.resolve_requests.append(request)
        authorities = tuple(
            ResearchObjectAuthority(
                dataset_id=item.dataset_id,
                authority_id=f"provisional-{item.dataset_id}",
                snapshot=ResearchObjectSnapshot(
                    dataset_id=item.dataset_id,
                    size_bytes=1,
                    etag="etag",
                    version_id=None,
                    last_modified=None,
                    placeholder=False,
                    snapshot_digest="snapshot",
                ),
            )
            for item in request.objects
        )
        for authority in authorities:
            self.active[authority.authority_id] = (
                request.parent_run_id,
                request.execution_fingerprint,
            )
        return authorities

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return supplied authorities for this validation-only fake."""
        return request.authorities

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Revoke only authorities with their original exact scope."""
        self.revoke_requests.append(request)
        for authority_id in request.authority_ids:
            scope = self.active.get(authority_id)
            assert scope == (
                request.parent_run_id,
                request.execution_fingerprint,
            )
            self.active.pop(authority_id, None)


@pytest.mark.asyncio
async def test_preflight_metadata_grants_are_released_before_cancel(
    tmp_path: Path,
) -> None:
    """Admission validation cannot leave provisional grants behind."""
    parsed = parse_research_input("question\nobs://bucket/input.tsv", "bucket")
    request = ResearchInventoryRequest(
        parsed_input=parsed,
        managed_assets=(),
        configured_bucket="bucket",
        max_managed_references=64,
        max_pasted_references=128,
        max_combined_references=256,
    )
    port = _ProvisionalScopeMetadataPort()

    await validate_research_inventory(request, port)

    assert len(port.resolve_requests) == 1
    assert len(port.revoke_requests) == 1
    resolved = port.resolve_requests[0]
    revoked = port.revoke_requests[0]
    assert revoked.parent_run_id == resolved.parent_run_id
    assert revoked.execution_fingerprint == resolved.execution_fingerprint
    assert not port.active

    store, _ = _seed_research_parent(tmp_path, "run-after-preflight")
    assert cancel_research_run(
        store, "run-after-preflight", "u1", 0
    ).status == ("cancelled")


def test_cancel_research_run_cas_marks_private_work_and_parent_terminal(
    tmp_path: Path,
) -> None:
    """Cancellation commits once and makes late local completion impossible."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-before-send", outbox_state="pending"
    )

    outcome = cancel_research_run(store, "run-cancel-before-send", "u1", 0)

    assert outcome.status == "cancelled"
    assert outcome.replay is False
    assert outcome.revision == 1
    assert _run_row(db_path, "run-cancel-before-send") == (
        "cancelled",
        None,
        1,
    )
    with closed_sqlite_connection(db_path) as connection:
        resolution = connection.execute(
            "SELECT cancel_requested FROM research_input_resolutions "
            "WHERE run_id = ?",
            ("run-cancel-before-send",),
        ).fetchone()
        root = connection.execute(
            "SELECT state FROM research_work_units WHERE run_id = ? "
            "AND kind = 'resolve_root'",
            ("run-cancel-before-send",),
        ).fetchone()
        outbox = connection.execute(
            "SELECT state FROM research_dispatch_outbox WHERE run_id = ?",
            ("run-cancel-before-send",),
        ).fetchone()
    assert resolution == (1,)
    assert root == ("cancelled",)
    assert outbox == ("cancelled",)

    replay = cancel_research_run(
        store, "run-cancel-before-send", "u1", outcome.revision
    )
    assert replay.status == "cancelled"
    assert replay.replay is True
    assert replay.revision == outcome.revision


def test_cancel_research_run_cascades_sent_child(
    tmp_path: Path,
) -> None:
    """A sent child is cancelled with the parent instead of conflicting."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-after-send", outbox_state="sent"
    )

    outcome = cancel_research_run(store, "run-cancel-after-send", "u1", 0)

    assert outcome.status == "cancelled"
    assert _run_row(db_path, "run-cancel-after-send") == (
        "cancelled",
        None,
        1,
    )
    with closed_sqlite_connection(db_path) as connection:
        outbox = connection.execute(
            "SELECT state FROM research_dispatch_outbox WHERE run_id = ?",
            ("run-cancel-after-send",),
        ).fetchone()
    assert outbox == ("cancelled",)


@pytest.mark.asyncio
async def test_late_work_completion_cannot_revive_cancelled_parent(
    tmp_path: Path,
) -> None:
    """The existing work-unit CAS must discard a callback after cancel."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-late-result", outbox_state=None
    )
    outcome = cancel_research_run(store, "run-cancel-late-result", "u1", 0)

    record = store.claim_work(
        "run-cancel-late-result:resolve_root",
        "late-worker",
        datetime(2026, 8, 9, tzinfo=UTC),
    )
    assert record is None
    assert outcome.status == "cancelled"
    assert _run_row(db_path, "run-cancel-late-result")[0] == "cancelled"


def test_cancel_wins_against_a_claimed_callback_completion_race(
    tmp_path: Path,
) -> None:
    """A callback that became ready before cancellation cannot settle work.

    This catches a lost cancellation fence: if ``complete_work`` ignores the
    cancellation CAS, the late worker would write a succeeded root after the
    parent has become terminal.
    """
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-race", outbox_state=None
    )
    claimed = store.claim_work(
        "run-cancel-race:resolve_root",
        "callback-worker",
        datetime(2026, 8, 9, tzinfo=UTC),
    )
    assert claimed is not None

    outcome, completion = _race_claimed_callback_with_cancellation(
        store, claimed
    )

    assert outcome.status == "cancelled"
    assert completion is False
    assert _run_row(db_path, "run-cancel-race") == ("cancelled", None, 1)
    with closed_sqlite_connection(db_path) as connection:
        root = connection.execute(
            "SELECT state, output_json FROM research_work_units "
            "WHERE unit_id = ?",
            ("run-cancel-race:resolve_root",),
        ).fetchone()
        children = connection.execute(
            "SELECT COUNT(*) FROM research_dispatch_outbox WHERE run_id = ?",
            ("run-cancel-race",),
        ).fetchone()
        work_units = connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = ?",
            ("run-cancel-race",),
        ).fetchone()
    assert root == ("cancelled", None)
    assert children == (0,)
    assert work_units == (1,)


@pytest.mark.asyncio
async def test_late_metadata_callback_stops_before_resolution_persistence(
    tmp_path: Path,
) -> None:
    """A callback that crosses cancellation cannot reach the next stage."""
    store, _db_path = _seed_research_parent(
        tmp_path, "run-cancel-callback", outbox_state=None
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def metadata(_request: object) -> object:
        entered.set()
        await release.wait()
        return object()

    request = ResearchCoordinatorRequest(
        "run-cancel-callback",
        object(),
        dependencies=ResearchCoordinatorDependencies(build_inventory=metadata),
    )
    task = asyncio.create_task(
        ResearchInputCoordinator(request, store=store).run(
            "run-cancel-callback", "worker"
        )
    )
    await entered.wait()
    outcome = cancel_research_run(store, "run-cancel-callback", "u1", 0)
    release.set()
    with pytest.raises(Exception) as caught:
        await task

    assert outcome.status == "cancelled"
    assert getattr(caught.value, "code") == "research_cancel_conflict"


class _NoopRecoveryProvider:
    """Provider fixture for grant-only recovery passes."""

    async def invoke(self, work_input: object, policy: object) -> object:
        """Fail if grant recovery accidentally invokes resolver work."""
        del work_input, policy
        raise AssertionError("grant recovery must not invoke provider work")

    async def query(self, request_identity: str) -> object | None:
        """Return no provider status for this grant-only fixture."""
        del request_identity
        return None


@pytest.mark.asyncio
async def test_recovery_retries_pending_grant_revoke_without_reopening_parent(
    tmp_path: Path,
) -> None:
    """A failed post-commit revoke remains pending for the next pass."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-grant", outbox_state=None
    )
    _seed_pending_grant_outbox(
        db_path,
        "run-cancel-grant",
        ("grant-1",),
    )
    cancel_research_run(store, "run-cancel-grant", "u1", 0)
    calls: list[ResearchGrantRevocation] = []

    async def revoke(request: ResearchGrantRevocation) -> None:
        calls.append(request)
        if len(calls) == 1:
            raise OSError("relay unavailable")

    service = ResearchRecoveryService(
        store,
        _NoopRecoveryProvider(),
        now=lambda: datetime(2026, 8, 9, tzinfo=UTC),
        grant_revoke=revoke,
    )
    await service.recover_once(datetime(2026, 8, 9, tzinfo=UTC))
    with closed_sqlite_connection(db_path) as connection:
        assert connection.execute(
            "SELECT state FROM research_grant_revocations "
            "WHERE run_id = 'run-cancel-grant'"
        ).fetchone() == ("pending",)

    await service.recover_once(datetime(2026, 8, 9, tzinfo=UTC))
    with closed_sqlite_connection(db_path) as connection:
        assert connection.execute(
            "SELECT state FROM research_grant_revocations "
            "WHERE run_id = 'run-cancel-grant'"
        ).fetchone() == ("revoked",)
    assert len(calls) == 2
    assert calls[0] == ResearchGrantRevocation(
        owner_run_id="run-cancel-grant",
        parent_run_id="run-cancel-grant",
        execution_fingerprint="fingerprint-1",
        grant_ids=("grant-1",),
    )
    assert _run_row(db_path, "run-cancel-grant")[0] == "cancelled"


def test_terminal_settlement_queues_child_scoped_grant_binding(
    tmp_path: Path,
) -> None:
    """Succeeded or failed Research parents retain exact revoke authority."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-terminal-grant", outbox_state=None
    )
    _seed_pending_grant_outbox(
        db_path,
        "run-terminal-grant",
        ("grant-final",),
        binding=("run-terminal-grant", "child-fingerprint"),
        state="accepted",
    )
    registry = RunRegistry(db_path)
    current = registry.get_run("run-terminal-grant", owner="u1")
    assert current is not None

    assert registry.settle_run(
        "run-terminal-grant",
        owner="u1",
        status="failed",
        result={},
        error="task_failed",
        expected_revision=current.revision,
    )
    settled = registry.get_run("run-terminal-grant", owner="u1")

    assert settled is not None and settled.status == "failed"
    assert store.pending_grant_revocations() == (
        ResearchGrantRevocation(
            owner_run_id="run-terminal-grant",
            parent_run_id="run-terminal-grant",
            execution_fingerprint="child-fingerprint",
            grant_ids=("grant-final",),
        ),
    )


@pytest.mark.asyncio
async def test_production_runtime_wires_metadata_revoke_for_cancelled_grants(
    tmp_path: Path,
) -> None:
    """Production recovery uses the direct/relay metadata revoke seam."""
    store, db_path = _seed_research_parent(
        tmp_path, "run-cancel-production-grant", outbox_state=None
    )
    authority_db = str(tmp_path / "independent-authority.sqlite3")
    authority_parent = "inventory-authority-scope"
    authority_store, grant_id = _seed_authority_grant(
        authority_db, authority_parent, "fingerprint-1"
    )
    _seed_pending_grant_outbox(
        db_path,
        "run-cancel-production-grant",
        (grant_id,),
        binding=(authority_parent, "fingerprint-1"),
    )
    cancel_research_run(store, "run-cancel-production-grant", "u1", 0)

    async def revoke(request: ResearchObjectRevokeRequest) -> None:
        """Forward the typed callback to the independent authority store."""
        authority_store.revoke(
            ResearchGrantRevoke(
                principal_key_prefix="ptm_test",
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                grant_ids=request.authority_ids,
            ),
            datetime(2026, 8, 9, tzinfo=UTC),
        )

    metadata = SimpleNamespace(
        revoke=AsyncMock(side_effect=revoke),
        resolve=AsyncMock(),
        verify=AsyncMock(),
    )
    runtime = build_research_dispatch_runtime(
        store,
        _NoopRecoveryProvider(),
        analyst_agent=SimpleNamespace(),
        analyst_config=SimpleNamespace(),
        sensitive_config=SimpleNamespace(),
        metadata_port=metadata,
        now=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    await runtime.recovery.recover_once(datetime(2026, 8, 9, tzinfo=UTC))

    metadata.revoke.assert_awaited_once_with(
        ResearchObjectRevokeRequest(
            parent_run_id=authority_parent,
            execution_fingerprint="fingerprint-1",
            authority_ids=(grant_id,),
        )
    )
    with closed_sqlite_connection(db_path) as connection:
        assert connection.execute(
            "SELECT state FROM research_grant_revocations "
            "WHERE run_id = 'run-cancel-production-grant'"
        ).fetchone() == ("revoked",)
    with closed_sqlite_connection(authority_store.db_path) as connection:
        assert connection.execute(
            "SELECT state FROM research_object_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone() == ("revoked",)
