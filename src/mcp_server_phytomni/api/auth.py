# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite-backed per-user API key store and inbound auth resolver.

Classes: ApiPrincipal, CreatedApiKey, ApiKeyRecord, ApiKeyStore.
Functions: get_key_store, resolve_principal, require_principal.

Keys are stored as PBKDF2-HMAC-SHA256 with a per-key salt; the plaintext
key is returned once at creation and never persisted or logged.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict

from ..config.defaults import ApiConfig
from ..runtime.request_context import bind_request_user

__all__ = [
    "ApiPrincipal",
    "CreatedApiKey",
    "ApiKeyRecord",
    "ApiKeyStore",
    "get_key_store",
    "resolve_principal",
    "require_principal",
    "scopes_satisfy",
    "relay_scope_satisfied",
]

_KEY_PREFIX = "ptm_"
_PREFIX_LEN = 12
_PBKDF2_ITERATIONS = 200_000
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _unauthorized() -> HTTPException:
    """Build a uniform 401 with a Bearer challenge header."""
    return HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers=dict(_UNAUTHORIZED_HEADERS),
    )


def _is_expired(expires_at: str | None) -> bool:
    """Return True when an ISO expiry timestamp is in the past."""
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now(UTC)


def _pbkdf2(key: str, salt: str) -> str:
    """Return the hex PBKDF2-HMAC-SHA256 digest of a key and hex salt."""
    return hashlib.pbkdf2_hmac(
        "sha256", key.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()


def _serialize_scopes(scopes: Sequence[str] | None) -> str | None:
    """Serialize a scope set to JSON; None for an all-access key."""
    if not scopes:
        return None
    return json.dumps(sorted(set(scopes)))


def _parse_scopes(raw: str | None) -> frozenset[str]:
    """Parse a stored scope string; None or empty means all access."""
    if not raw:
        return frozenset()
    return frozenset(json.loads(raw))


def scopes_satisfy(granted: frozenset[str], needed: Sequence[str]) -> bool:
    """Return True when granted scopes authorize every needed scope.

    An empty granted set means all access (back-compat for keys minted
    before scopes existed). A ``relay:*`` wildcard authorizes any
    ``relay:<service>`` need.

    Args:
        granted: The principal's granted scopes (empty means all access).
        needed: The scopes a route requires.

    Returns:
        True when access is allowed, False otherwise.
    """
    if not granted:
        return True
    for scope in needed:
        if scope in granted:
            continue
        if scope.startswith("relay:") and "relay:*" in granted:
            continue
        return False
    return True


def relay_scope_satisfied(granted: frozenset[str], service: str) -> bool:
    """Return True only when granted explicitly authorizes a relay service.

    Unlike ``scopes_satisfy``, an EMPTY granted set does NOT mean all
    access here: relay routes inject the operator's real upstream
    credentials, so a legacy scope-less key must be denied rather than
    silently granted every upstream. A ``relay:*`` wildcard authorizes
    any service; otherwise the exact ``relay:<service>`` scope is
    required.

    Args:
        granted: The principal's granted scopes (empty means deny here).
        service: The relay service the route fronts (e.g. ``llm``).

    Returns:
        True only when ``relay:<service>`` or ``relay:*`` is granted.
    """
    return f"relay:{service}" in granted or "relay:*" in granted


@dataclass(frozen=True)
class ApiPrincipal:
    """The authenticated caller resolved from a valid API key.

    Attributes:
        user_id: The user the key is bound to.
        key_prefix: The presented key's public prefix, for audit/logging.
        scopes: The granted scopes; an empty set means all access
            (back-compat for keys minted before scopes existed).
    """

    user_id: str
    key_prefix: str
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CreatedApiKey:
    """A freshly minted key; the plaintext is shown exactly once.

    Attributes:
        api_key: The full plaintext key (never persisted).
        prefix: The stored public lookup prefix.
        user_id: The bound user id.
    """

    api_key: str
    prefix: str
    user_id: str


class ApiKeyRecord(BaseModel):
    """A non-secret view of a stored key for listing.

    Carries no hash or salt so listing can never leak key material.

    Attributes:
        user_id: The bound user id.
        name: Optional human label.
        prefix: The public lookup prefix.
        created_at: ISO-8601 creation timestamp.
        revoked_at: ISO-8601 revoke timestamp, or None.
        last_used_at: ISO-8601 last successful auth, or None.
        expires_at: ISO-8601 expiry, or None.
        scopes: Granted scopes; an empty set means all access.
        active: Derived; True when neither revoked nor expired.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    name: str | None
    prefix: str
    created_at: str
    revoked_at: str | None
    last_used_at: str | None
    expires_at: str | None
    scopes: frozenset[str] = frozenset()

    @property
    def active(self) -> bool:
        """Return True when the key is neither revoked nor expired."""
        return self.revoked_at is None and not _is_expired(self.expires_at)


class ApiKeyStore:
    """SQLite store mapping hashed API keys to user ids.

    The database must stay on a local filesystem; network filesystems
    deadlock under SQLite WAL.

    Attributes:
        db_path: Filesystem path to the SQLite database.
    """

    def __init__(self, db_path: str) -> None:
        """Initialize the store, creating the schema if needed.

        Args:
            db_path: SQLite database path for the key store.
        """
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT,
                    key_prefix TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_used_at TEXT,
                    expires_at TEXT
                )
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_prefix "
                "ON api_keys(key_prefix)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_user "
                "ON api_keys(user_id)"
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(api_keys)")
            }
            if "scopes" not in columns:
                conn.execute("ALTER TABLE api_keys ADD COLUMN scopes TEXT")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a short-lived autocommit WAL connection.

        Auth lookups are low frequency relative to agent calls, so a
        fresh connection per operation is used instead of the pooled
        thread-local model in func_cache.
        """
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    def create(
        self,
        user_id: str,
        name: str | None = None,
        expires_at: datetime | None = None,
        scopes: Sequence[str] | None = None,
    ) -> CreatedApiKey:
        """Mint and persist a new key, returning the one-time plaintext.

        Args:
            user_id: The user the key authenticates.
            name: Optional human label.
            expires_at: Optional aware datetime after which the key fails.
            scopes: Optional granted scopes; None or empty mints an
                all-access key for backward compatibility.

        Returns:
            The created key; ``api_key`` is the only time the plaintext
            is available.
        """
        api_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        prefix = api_key[:_PREFIX_LEN]
        salt = secrets.token_hex(16)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (
                    user_id, name, key_prefix, salt, key_hash,
                    created_at, expires_at, scopes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name,
                    prefix,
                    salt,
                    _pbkdf2(api_key, salt),
                    _now_iso(),
                    expires_at.isoformat() if expires_at else None,
                    _serialize_scopes(scopes),
                ),
            )
        return CreatedApiKey(api_key=api_key, prefix=prefix, user_id=user_id)

    def resolve_key(self, presented_key: str) -> ApiPrincipal:
        """Resolve a plaintext key to its principal or raise 401.

        Args:
            presented_key: The plaintext key supplied by the caller.

        Returns:
            The authenticated principal.

        Raises:
            HTTPException: 401 when the key is unknown, revoked, or
                expired.
        """
        prefix = presented_key[:_PREFIX_LEN]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, salt, key_hash, revoked_at,
                       expires_at, scopes
                FROM api_keys WHERE key_prefix = ?
                """,
                (prefix,),
            ).fetchall()
            for row in rows:
                row_id, user_id, salt, key_hash, revoked, expires, scopes = row
                if not secrets.compare_digest(
                    _pbkdf2(presented_key, salt), key_hash
                ):
                    continue
                if revoked is not None or _is_expired(expires):
                    raise _unauthorized()
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (_now_iso(), row_id),
                )
                return ApiPrincipal(
                    user_id=user_id,
                    key_prefix=prefix,
                    scopes=_parse_scopes(scopes),
                )
        raise _unauthorized()

    def list(
        self,
        user_id: str | None = None,
        active_only: bool = True,
    ) -> list[ApiKeyRecord]:
        """List stored keys without any secret material.

        Args:
            user_id: Optional filter to a single user.
            active_only: When True (default), exclude revoked and
                expired keys from the result.

        Returns:
            Non-secret key records ordered by creation time.
        """
        query = (
            "SELECT user_id, name, key_prefix, created_at, revoked_at, "
            "last_used_at, expires_at, scopes FROM api_keys"
        )
        conditions: list[str] = []
        params: list[str] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if active_only:
            conditions.append("revoked_at IS NULL")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        records = [
            ApiKeyRecord(
                user_id=row[0],
                name=row[1],
                prefix=row[2],
                created_at=row[3],
                revoked_at=row[4],
                last_used_at=row[5],
                expires_at=row[6],
                scopes=_parse_scopes(row[7]),
            )
            for row in rows
        ]
        if active_only:
            records = [r for r in records if r.active]
        return records

    def revoke(self, prefix: str) -> bool:
        """Revoke the active key with the given prefix.

        Args:
            prefix: The public key prefix to revoke.

        Returns:
            True when an active key was revoked, False otherwise.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET revoked_at = ? "
                "WHERE key_prefix = ? AND revoked_at IS NULL",
                (_now_iso(), prefix),
            )
            return cursor.rowcount > 0


@cache
def get_key_store(db_path: str) -> ApiKeyStore:
    """Return a process-cached store for a resolved database path.

    Args:
        db_path: SQLite database path for the key store.

    Returns:
        A shared ``ApiKeyStore`` so the schema is initialized once.
    """
    return ApiKeyStore(str(Path(db_path).resolve()))


def _extract_token(
    authorization: str | None, x_api_key: str | None
) -> str | None:
    """Pull the presented key from Bearer or X-API-Key headers."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :].strip()
        return token or None
    if x_api_key:
        token = x_api_key.strip()
        return token or None
    return None


def resolve_principal(
    store: ApiKeyStore,
    authorization: str | None,
    x_api_key: str | None,
) -> ApiPrincipal:
    """Resolve a request's credentials to a principal or raise 401.

    Args:
        store: The key store to validate against.
        authorization: Raw ``Authorization`` header value, if any.
        x_api_key: Raw ``X-API-Key`` header value, if any.

    Returns:
        The authenticated principal.

    Raises:
        HTTPException: 401 when credentials are missing or invalid.
    """
    token = _extract_token(authorization, x_api_key)
    if token is None:
        raise _unauthorized()
    return store.resolve_key(token)


async def require_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ApiPrincipal:
    """FastAPI dependency resolving the caller from request headers.

    Args:
        authorization: Bearer authorization header.
        x_api_key: Alternative X-API-Key header.

    Returns:
        The authenticated principal for the configured key store.
    """
    store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
    principal = resolve_principal(store, authorization, x_api_key)
    # Bind for downstream handlers/wrappers. request_context_middleware
    # brackets this contextvar (bound to None then reset on request
    # exit), so no explicit reset is needed here.
    bind_request_user(principal.user_id)
    return principal
