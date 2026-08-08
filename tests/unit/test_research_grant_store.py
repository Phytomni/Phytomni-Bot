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
    ResearchObjectSnapshot,
)

pytestmark = pytest.mark.unit


def _now() -> datetime:
    """Return a fixed UTC time for deterministic grant lifetimes."""
    return datetime(2026, 8, 8, 0, 0, tzinfo=UTC)


def _snapshot(
    *,
    etag: str = "metadata-etag",
    snapshot_digest: str = "observed-snapshot-digest",
) -> ResearchObjectSnapshot:
    """Build one metadata-port snapshot with independently fixed values."""
    return ResearchObjectSnapshot(
        dataset_id="dataset-001",
        size_bytes=73,
        etag=etag,
        version_id="metadata-v1",
        last_modified="2026-08-08T00:00:00+00:00",
        placeholder=False,
        snapshot_digest=snapshot_digest,
    )


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
        authorities=(
            ResearchObjectAuthority(
                dataset_id="dataset-001",
                authority_id="metadata-port-authority",
                snapshot=_snapshot(),
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
    assert grant.snapshot == _snapshot()
    assert not hasattr(grant, "exact_reference")
    assert "obs://" not in repr(grant)
    assert grant.state == "active"
    assert grant.revision == 1
    assert grant.expires_at == now + RESEARCH_GRANT_TTL


def test_resolve_persists_real_authority_snapshot_without_public_reference(
    tmp_path: Path,
) -> None:
    """A grant keeps observed metadata while hiding its raw OBS reference."""
    candidate = ResearchObjectCandidate(
        dataset_id="dataset-001",
        exact_reference="obs://private-bucket/inputs/leaf.tsv",
        compound_suffix=".tsv",
    )
    snapshot = _snapshot()
    request = ResearchGrantResolve(
        principal_key_prefix="ptm_test",
        parent_run_id="run-001",
        execution_fingerprint="execution-sha256",
        objects=(candidate,),
        authorities=(
            ResearchObjectAuthority(
                dataset_id="dataset-001",
                authority_id="metadata-port-authority",
                snapshot=snapshot,
            ),
        ),
    )

    grant = ResearchGrantStore(
        str(tmp_path / "relay.sqlite3")
    ).resolve_or_replay(request, _now())[0]

    assert grant.snapshot == snapshot
    assert not hasattr(grant, "exact_reference")
    assert "obs://" not in repr(grant)


def test_resolve_rejects_candidates_without_real_metadata_authorities(
    tmp_path: Path,
) -> None:
    """The grant store never substitutes zero/null synthetic metadata."""
    request = _request()
    missing_authority = ResearchGrantResolve(
        principal_key_prefix=request.principal_key_prefix,
        parent_run_id=request.parent_run_id,
        execution_fingerprint=request.execution_fingerprint,
        objects=request.objects,
    )

    with pytest.raises(ResearchGrantError):
        ResearchGrantStore(str(tmp_path / "relay.sqlite3")).resolve_or_replay(
            missing_authority, _now()
        )


def test_initialization_additively_upgrades_legacy_grant_table(
    tmp_path: Path,
) -> None:
    """Same-table legacy grants are retained but made safely unusable."""
    database = tmp_path / "relay.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE research_object_grants (grant_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO research_object_grants VALUES ('legacy-grant')"
        )

    store = ResearchGrantStore(str(database))
    ResearchGrantStore(str(database))

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(research_object_grants)"
            )
        }
        legacy = connection.execute(
            "SELECT grant_id, state, grant_schema_version "
            "FROM research_object_grants WHERE grant_id = 'legacy-grant'"
        ).fetchone()
        schema_version = connection.execute(
            "SELECT version FROM research_grant_schema_versions "
            "WHERE schema_name = 'research_object_grants'"
        ).fetchone()

    assert {"grant_id", "exact_reference", "snapshot_json"} <= columns
    assert legacy == ("legacy-grant", "expired", 0)
    assert schema_version == (2,)
    assert len(store.resolve_or_replay(_request(), _now())) == 1


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


def test_verify_rejects_changed_real_metadata_port_snapshot(
    tmp_path: Path,
) -> None:
    """A changed real metadata snapshot cannot reuse the original grant."""
    store = ResearchGrantStore(str(tmp_path / "relay.sqlite3"))
    grant = store.resolve_or_replay(_request(), _now())[0]

    with pytest.raises(ResearchGrantError):
        store.verify_or_rotate(
            _verify(
                grant.grant_id,
                snapshot=_snapshot(
                    etag="changed-etag",
                    snapshot_digest="changed-snapshot-digest",
                ),
            ),
            _now(),
        )


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
    assert not hasattr(rotated, "exact_reference")
    assert "obs://" not in repr(rotated)
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
