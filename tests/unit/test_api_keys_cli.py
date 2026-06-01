# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the API key management CLI.

Covers one-time plaintext output on create, redaction on list, and that
revoke disables the key for subsequent resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.auth import ApiKeyStore, resolve_principal
from mcp_server_phytomni.api.keys import main

pytestmark = pytest.mark.unit


def _use_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI's key store at a throwaway SQLite file."""
    db = tmp_path / "api_keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    return db


def _full_key(out: str) -> str:
    """Return the single full ptm_ key token from CLI output."""
    return next(t for t in out.split() if t.startswith("ptm_") and len(t) > 12)


def test_create_prints_plaintext_key_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify create emits a usable ptm_ key once."""
    db = _use_db(tmp_path, monkeypatch)

    code = main(["create", "--user-id", "alice", "--name", "ci"])

    assert code == 0
    out = capsys.readouterr().out
    full_keys = [
        t for t in out.split() if t.startswith("ptm_") and len(t) > 12
    ]
    assert len(full_keys) == 1

    principal = resolve_principal(
        ApiKeyStore(str(db)), f"Bearer {full_keys[0]}", None
    )
    assert principal.user_id == "alice"


def test_list_never_prints_plaintext_or_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify list shows metadata but no secret material."""
    db = _use_db(tmp_path, monkeypatch)

    main(["create", "--user-id", "bob"])
    assert db.exists()
    full_key = _full_key(capsys.readouterr().out)

    code = main(["list", "--user-id", "bob"])

    assert code == 0
    listed = capsys.readouterr().out
    assert "bob" in listed
    assert full_key not in listed
    assert full_key[:12] in listed


def test_revoke_disables_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify a revoked prefix can no longer authenticate."""
    db = _use_db(tmp_path, monkeypatch)

    main(["create", "--user-id", "carol"])
    full_key = _full_key(capsys.readouterr().out)

    assert main(["revoke", "--prefix", full_key[:12]]) == 0

    with pytest.raises(HTTPException) as exc:
        resolve_principal(ApiKeyStore(str(db)), f"Bearer {full_key}", None)
    assert exc.value.status_code == 401


def test_unknown_command_returns_nonzero() -> None:
    """Verify an unknown subcommand exits non-zero."""
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code != 0


def test_create_with_scopes_binds_them_to_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify --scope mints a key resolving with exactly those scopes."""
    db = _use_db(tmp_path, monkeypatch)

    code = main(
        [
            "create",
            "--user-id",
            "dave",
            "--scope",
            "relay:llm",
            "--scope",
            "agents",
        ]
    )

    assert code == 0
    full_key = _full_key(capsys.readouterr().out)
    principal = resolve_principal(
        ApiKeyStore(str(db)), f"Bearer {full_key}", None
    )
    assert principal.scopes == frozenset({"relay:llm", "agents"})


def test_create_without_scopes_is_all_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify a scope-less key resolves to the empty all-access set."""
    db = _use_db(tmp_path, monkeypatch)

    main(["create", "--user-id", "erin"])
    full_key = _full_key(capsys.readouterr().out)

    principal = resolve_principal(
        ApiKeyStore(str(db)), f"Bearer {full_key}", None
    )
    assert principal.scopes == frozenset()


def test_list_shows_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify list renders a key's granted scopes."""
    _use_db(tmp_path, monkeypatch)

    main(["create", "--user-id", "frank", "--scope", "relay:bi"])
    capsys.readouterr()

    code = main(["list", "--user-id", "frank"])

    assert code == 0
    listed = capsys.readouterr().out
    assert "relay:bi" in listed
