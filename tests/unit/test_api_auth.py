# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the SQLite per-user API key store and auth resolver.

Covers key creation (hash-only persistence), bearer/x-api-key resolution,
revoke and expiry handling, last-used tracking, and listing without secrets.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.auth import (
    ApiKeyStore,
    ApiPrincipal,
    resolve_principal,
)

pytestmark = pytest.mark.unit


def _make_store(tmp_path: Path) -> ApiKeyStore:
    """Return an ApiKeyStore backed by a throwaway SQLite file."""
    return ApiKeyStore(str(tmp_path / "api_keys.sqlite"))


def test_create_returns_prefixed_key_and_persists_only_hash(
    tmp_path: Path,
) -> None:
    """Verify create() returns a ptm_ key and stores no plaintext."""
    store = _make_store(tmp_path)

    created = store.create(user_id="alice", name="laptop")

    assert created.api_key.startswith("ptm_")
    assert created.prefix == created.api_key[:12]
    assert created.user_id == "alice"

    conn = sqlite3.connect(str(tmp_path / "api_keys.sqlite"))
    rows = conn.execute(
        "SELECT key_hash, salt, key_prefix FROM api_keys"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    key_hash, salt, prefix = rows[0]
    assert created.api_key not in (key_hash, salt)
    assert prefix == created.prefix


def test_resolve_valid_key_returns_principal_and_marks_used(
    tmp_path: Path,
) -> None:
    """Verify a valid key resolves to its principal and sets last_used."""
    store = _make_store(tmp_path)
    created = store.create(user_id="bob")

    principal = resolve_principal(
        store, authorization=f"Bearer {created.api_key}", x_api_key=None
    )

    assert principal == ApiPrincipal(user_id="bob", key_prefix=created.prefix)
    record = next(r for r in store.list() if r.prefix == created.prefix)
    assert record.last_used_at is not None
    assert record.active is True


def test_resolve_accepts_x_api_key_header(tmp_path: Path) -> None:
    """Verify the X-API-Key header is accepted as well as Bearer."""
    store = _make_store(tmp_path)
    created = store.create(user_id="carol")

    principal = resolve_principal(
        store, authorization=None, x_api_key=created.api_key
    )

    assert principal.user_id == "carol"


def test_resolve_missing_credentials_raises_401(tmp_path: Path) -> None:
    """Verify absent credentials raise 401 with a Bearer challenge."""
    store = _make_store(tmp_path)

    with pytest.raises(HTTPException) as exc:
        resolve_principal(store, authorization=None, x_api_key=None)

    assert exc.value.status_code == 401
    headers = exc.value.headers
    assert headers is not None
    assert headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("bad", ["Bearer not-a-key", "garbage", "Bearer "])
def test_resolve_unknown_key_raises_401(tmp_path: Path, bad: str) -> None:
    """Verify unknown or malformed credentials raise 401."""
    store = _make_store(tmp_path)
    store.create(user_id="dave")

    with pytest.raises(HTTPException) as exc:
        resolve_principal(store, authorization=bad, x_api_key=None)

    assert exc.value.status_code == 401


def test_resolve_revoked_key_raises_401(tmp_path: Path) -> None:
    """Verify a revoked key no longer resolves."""
    store = _make_store(tmp_path)
    created = store.create(user_id="erin")
    assert store.revoke(created.prefix) is True

    with pytest.raises(HTTPException) as exc:
        resolve_principal(
            store, authorization=f"Bearer {created.api_key}", x_api_key=None
        )

    assert exc.value.status_code == 401


def test_resolve_expired_key_raises_401(tmp_path: Path) -> None:
    """Verify an expired key is rejected."""
    store = _make_store(tmp_path)
    past = datetime.now(UTC) - timedelta(days=1)
    created = store.create(user_id="frank", expires_at=past)

    with pytest.raises(HTTPException) as exc:
        resolve_principal(
            store, authorization=f"Bearer {created.api_key}", x_api_key=None
        )

    assert exc.value.status_code == 401


def test_list_excludes_secret_material(tmp_path: Path) -> None:
    """Verify list() never exposes plaintext, hash, or salt."""
    store = _make_store(tmp_path)
    store.create(user_id="grace", name="ci")

    records = store.list(user_id="grace")

    assert len(records) == 1
    fields = vars(records[0])
    assert "key_hash" not in fields
    assert "salt" not in fields
    assert records[0].user_id == "grace"
    assert records[0].name == "ci"
    assert records[0].active is True
