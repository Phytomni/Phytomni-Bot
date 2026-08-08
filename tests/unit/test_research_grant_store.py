# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the operator-private run-bound Research grant store."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.api.relay.research_grants import (
    RESEARCH_GRANT_PURGE_GRACE,
    RESEARCH_GRANT_ROTATE_BEFORE,
    RESEARCH_GRANT_TTL,
    ResearchGrantError,
    ResearchGrantResolve,
    ResearchGrantRevoke,
    ResearchGrantStore,
    ResearchGrantVerify,
)
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
)

pytestmark = pytest.mark.unit


def _now() -> datetime:
    """Return a fixed UTC time for deterministic grant lifetimes."""
    return datetime(2026, 8, 8, 0, 0, tzinfo=UTC)


def _request() -> ResearchGrantResolve:
    """Build one trusted, run-bound object-resolution request."""
    return ResearchGrantResolve(
        principal_key_prefix="ptm_test",
        parent_run_id="run-001",
        execution_fingerprint="execution-sha256",
        objects=(
            ResearchObjectCandidate(
                dataset_id="dataset-001",
                exact_reference="obs://private-bucket/inputs/leaf.tsv",
                compound_suffix=".tsv",
            ),
        ),
    )


def _verify(
    grant_id: str,
    *,
    principal_key_prefix: str = "ptm_test",
    parent_run_id: str = "run-001",
    execution_fingerprint: str = "execution-sha256",
    snapshot=None,
) -> ResearchGrantVerify:
    """Build one request that proves a previously resolved grant."""
    if snapshot is None:
        raise AssertionError("A verified grant requires its safe snapshot.")
    return ResearchGrantVerify(
        principal_key_prefix=principal_key_prefix,
        parent_run_id=parent_run_id,
        execution_fingerprint=execution_fingerprint,
        authorities=(
            ResearchObjectAuthority(
                dataset_id="dataset-001",
                authority_id=grant_id,
                snapshot=snapshot,
            ),
        ),
    )


def test_resolve_persists_run_bound_grant_with_fixed_lifetime(
    tmp_path: Path,
) -> None:
    """A resolve creates one opaque grant bound to the request identity."""
    now = _now()
    grants = ResearchGrantStore(
        str(tmp_path / "relay.sqlite3")
    ).resolve_or_replay(_request(), now)

    assert len(grants) == 1
    grant = grants[0]
    assert grant.grant_id
    assert grant.principal_key_prefix == "ptm_test"
    assert grant.parent_run_id == "run-001"
    assert grant.execution_fingerprint == "execution-sha256"
    assert grant.dataset_id == "dataset-001"
    assert grant.exact_reference == "obs://private-bucket/inputs/leaf.tsv"
    assert grant.state == "active"
    assert grant.revision == 1
    assert grant.expires_at == now + RESEARCH_GRANT_TTL


def test_initialization_is_additive_for_empty_legacy_and_repeated_databases(
    tmp_path: Path,
) -> None:
    """Grant initialization preserves legacy rows and can run repeatedly."""
    database = tmp_path / "relay.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('keep')")

    ResearchGrantStore(str(database))
    ResearchGrantStore(str(database))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT * FROM legacy_marker"
        ).fetchall() == [("keep",)]
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(research_object_grants)"
            )
        }
    assert {"grant_id", "exact_reference", "snapshot_json"} <= columns


def test_resolve_replays_after_restart_and_under_concurrent_calls(
    tmp_path: Path,
) -> None:
    """One identity produces one durable grant under concurrent resolves."""
    database = tmp_path / "relay.sqlite3"
    request = _request()

    def resolve() -> str:
        return (
            ResearchGrantStore(str(database))
            .resolve_or_replay(request, _now())[0]
            .grant_id
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        grant_ids = list(executor.map(lambda _value: resolve(), range(16)))

    assert set(grant_ids) == {
        ResearchGrantStore(str(database))
        .resolve_or_replay(request, _now())[0]
        .grant_id
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_object_grants"
        ).fetchone() == (1,)


def test_verify_enforces_principal_run_execution_and_snapshot_bindings(
    tmp_path: Path,
) -> None:
    """A grant cannot cross a principal, parent-run, execution, or snapshot."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]

    for request in (
        _verify(
            grant.grant_id,
            principal_key_prefix="ptm_other",
            snapshot=grant.snapshot,
        ),
        _verify(
            grant.grant_id, parent_run_id="run-other", snapshot=grant.snapshot
        ),
        _verify(
            grant.grant_id,
            execution_fingerprint="execution-other",
            snapshot=grant.snapshot,
        ),
        ResearchGrantVerify(
            "ptm_test",
            "run-001",
            "execution-sha256",
            (
                ResearchObjectAuthority(
                    "dataset-other", grant.grant_id, grant.snapshot
                ),
            ),
        ),
    ):
        with pytest.raises(ResearchGrantError):
            store.verify_or_rotate(request, _now())


def test_verify_returns_unchanged_active_grant_before_rotation_window(
    tmp_path: Path,
) -> None:
    """Verification never extends a healthy grant's TTL in place."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]
    verified = store.verify_or_rotate(
        _verify(grant.grant_id, snapshot=grant.snapshot),
        _now()
        + RESEARCH_GRANT_TTL
        - RESEARCH_GRANT_ROTATE_BEFORE
        - timedelta(seconds=1),
    )

    assert verified == (grant,)


def test_verify_rotates_only_at_or_inside_the_fifteen_minute_window(
    tmp_path: Path,
) -> None:
    """Rotation creates a new grant and atomically revokes the old bearer."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]
    rotation_time = _now() + RESEARCH_GRANT_TTL - RESEARCH_GRANT_ROTATE_BEFORE
    rotated = store.verify_or_rotate(
        _verify(grant.grant_id, snapshot=grant.snapshot), rotation_time
    )[0]

    assert rotated.grant_id != grant.grant_id
    assert rotated.revision == grant.revision + 1
    assert rotated.expires_at == rotation_time + RESEARCH_GRANT_TTL
    with pytest.raises(ResearchGrantError):
        store.verify_or_rotate(
            _verify(grant.grant_id, snapshot=grant.snapshot), rotation_time
        )


def test_revoke_is_idempotent_and_foreign_owner_safe(tmp_path: Path) -> None:
    """Matching revocation repeats while foreign grant IDs fail closed."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]
    matching = ResearchGrantRevoke(
        "ptm_test", "run-001", "execution-sha256", (grant.grant_id,)
    )
    store.revoke(matching, _now())
    store.revoke(matching, _now())

    with pytest.raises(ResearchGrantError):
        store.revoke(
            ResearchGrantRevoke(
                "ptm_other", "run-001", "execution-sha256", (grant.grant_id,)
            ),
            _now(),
        )


def test_expired_or_revoked_grants_cannot_be_verified(tmp_path: Path) -> None:
    """Terminal grants deny subsequent verify or rotation requests."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    expired = store.resolve_or_replay(_request(), _now())[0]
    with pytest.raises(ResearchGrantError):
        store.verify_or_rotate(
            _verify(expired.grant_id, snapshot=expired.snapshot),
            _now() + RESEARCH_GRANT_TTL,
        )

    revoked = store.resolve_or_replay(_request(), _now())[0]
    store.revoke(
        ResearchGrantRevoke(
            "ptm_test", "run-001", "execution-sha256", (revoked.grant_id,)
        ),
        _now(),
    )
    with pytest.raises(ResearchGrantError):
        store.verify_or_rotate(
            _verify(revoked.grant_id, snapshot=revoked.snapshot), _now()
        )


def test_purge_keeps_terminal_grants_for_twenty_four_hour_grace(
    tmp_path: Path,
) -> None:
    """Terminal grants remain private for exactly the grace period."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]
    with pytest.raises(ResearchGrantError):
        store.verify_or_rotate(
            _verify(grant.grant_id, snapshot=grant.snapshot),
            grant.expires_at,
        )
    assert (
        store.purge_expired(grant.expires_at + RESEARCH_GRANT_PURGE_GRACE) == 0
    )
    assert (
        store.purge_expired(
            grant.expires_at
            + RESEARCH_GRANT_PURGE_GRACE
            + timedelta(microseconds=1)
        )
        == 1
    )
