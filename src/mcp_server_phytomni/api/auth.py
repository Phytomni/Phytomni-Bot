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
import secrets
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException

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
]

_KEY_PREFIX = "ptm_"
_PREFIX_LEN = 12
_PBKDF2_ITERATIONS = 200_000
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _unauthorized() -> HTTPException:
    """Build a uniform 401 with a Bearer challenge header."""
    return HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers=dict(_UNAUTHORIZED_HEADERS),
    )


def _is_expired(expires_at: Optional[str]) -> bool:
    """Return True when an ISO expiry timestamp is in the past."""
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)


def _pbkdf2(key: str, salt: str) -> str:
    """Return the hex PBKDF2-HMAC-SHA256 digest of a key and hex salt."""
    return hashlib.pbkdf2_hmac(
        "sha256", key.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()


@dataclass(frozen=True)
class ApiPrincipal:
    """The authenticated caller resolved from a valid API key.

    Attributes:
        user_id: The user the key is bound to.
        key_prefix: The presented key's public prefix, for audit/logging.
    """

    user_id: str
    key_prefix: str


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


@dataclass(frozen=True)
class ApiKeyRecord:
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
        active: Derived; True when neither revoked nor expired.
    """

    user_id: str
    name: Optional[str]
    prefix: str
    created_at: str
    revoked_at: Optional[str]
    last_used_at: Optional[str]
    expires_at: Optional[str]

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
        name: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> CreatedApiKey:
        """Mint and persist a new key, returning the one-time plaintext.

        Args:
            user_id: The user the key authenticates.
            name: Optional human label.
            expires_at: Optional aware datetime after which the key fails.

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
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name,
                    prefix,
                    salt,
                    _pbkdf2(api_key, salt),
                    _now_iso(),
                    expires_at.isoformat() if expires_at else None,
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
                SELECT id, user_id, salt, key_hash, revoked_at, expires_at
                FROM api_keys WHERE key_prefix = ?
                """,
                (prefix,),
            ).fetchall()
            for row in rows:
                row_id, user_id, salt, key_hash, revoked, expires = row
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
                return ApiPrincipal(user_id=user_id, key_prefix=prefix)
        raise _unauthorized()

    def list(self, user_id: Optional[str] = None) -> list[ApiKeyRecord]:
        """List stored keys without any secret material.

        Args:
            user_id: Optional filter to a single user.

        Returns:
            Non-secret key records ordered by creation time.
        """
        query = (
            "SELECT user_id, name, key_prefix, created_at, revoked_at, "
            "last_used_at, expires_at FROM api_keys"
        )
        params: tuple[str, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " ORDER BY created_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ApiKeyRecord(
                user_id=row[0],
                name=row[1],
                prefix=row[2],
                created_at=row[3],
                revoked_at=row[4],
                last_used_at=row[5],
                expires_at=row[6],
            )
            for row in rows
        ]

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


@lru_cache(maxsize=None)
def get_key_store(db_path: str) -> ApiKeyStore:
    """Return a process-cached store for a resolved database path.

    Args:
        db_path: SQLite database path for the key store.

    Returns:
        A shared ``ApiKeyStore`` so the schema is initialized once.
    """
    return ApiKeyStore(str(Path(db_path).resolve()))


def _extract_token(
    authorization: Optional[str], x_api_key: Optional[str]
) -> Optional[str]:
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
    authorization: Optional[str],
    x_api_key: Optional[str],
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
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
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
