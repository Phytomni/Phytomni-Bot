# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared runtime ID and path construction helpers.

Classes: PathPolicyError, IdFactory, RunIdentity.
Functions: resolve_user_id, safe_path_segment, run_root_key, task_root_key,
    task_output_key, shared_output_key, task_tmp_key, task_downloads_key.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from secrets import token_hex

from ..config.relay_mode import relay_mode_enabled

DEFAULT_USER_ID = "anonymous"
AGENT_DATA_ROOT = "agent_data"
USER_DATA_ROOT = f"{AGENT_DATA_ROOT}/user_data"

_SAFE_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class PathPolicyError(ValueError):
    """Raised when a runtime ID or path segment is unsafe."""


class IdFactory:
    """Factory for traceable runtime IDs without UUID-shaped path segments.

    Attributes:
        _now: Callable returning the current datetime.
        _token_factory: Callable returning random token text.
    """

    def __init__(
        self,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[int], str] = token_hex,
    ) -> None:
        """Initialize the factory with injectable clocks for tests.

        Args:
            now: Optional callable returning current datetime
                (default uses system clock).
            token_factory: Callable returning random token string
                (default uses token_hex).
        """
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory

    def current_utc(self) -> datetime:
        """Return the current time normalized to UTC.

        Returns:
            datetime: Current time in UTC timezone.
        """
        current = self._now()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def date_stamp(self, current: datetime | None = None) -> str:
        """Return a compact UTC date stamp.

        Args:
            current: Optional datetime to use instead of now (for testing).

        Returns:
            str: Date stamp in format YYYYMMDD.
        """
        return self._utc(current).strftime("%Y%m%d")

    def new_id(
        self,
        kind: str,
        *parts: object,
        current: datetime | None = None,
    ) -> str:
        """Return a timestamped ID with optional readable context parts.

        Args:
            kind: ID kind/scope (e.g., 'run', 'task').
            *parts: Optional path-safe string parts for readability.
            current: Optional datetime to use instead of now (for testing).

        Returns:
            str: Timestamp-based ID in format
                YYYYMMDDTHHMMSSZ-kind-parts-token.
        """
        timestamp = self._utc(current).strftime("%Y%m%dT%H%M%SZ")
        safe_parts = [safe_path_segment(kind, "id")]
        safe_parts.extend(
            safe_path_segment(part, "part")
            for part in parts
            if str(part or "").strip()
        )
        safe_parts.append(safe_path_segment(self._token_factory(4), "token"))
        return "-".join([timestamp, *safe_parts])

    def run_id(
        self,
        scope: str = "run",
        user_id: str | None = None,
        current: datetime | None = None,
    ) -> str:
        """Return a run ID suitable for task and temporary paths.

        Args:
            scope: ID scope (default 'run').
            user_id: Optional user ID to include in the run ID.
            current: Optional datetime to use instead of now (for testing).

        Returns:
            str: Run ID string.
        """
        if user_id:
            return self.new_id(scope, user_id, current=current)
        return self.new_id(scope, current=current)

    @staticmethod
    def _utc(current: datetime | None = None) -> datetime:
        """Return a provided timestamp normalized to UTC."""
        if current is None:
            return datetime.now(timezone.utc)
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)


@dataclass(frozen=True)
class RunIdentity:
    """Identity shared by all generated paths for one workflow run.

    Attributes:
        user_id: Sanitized user identifier used in run paths.
        run_id: Traceable run identifier shared across workflow outputs.
        created_at: UTC timestamp used for date-stamped path layout.
    """

    user_id: str
    run_id: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        user_id: str | None = None,
        scope: str = "run",
        id_factory: IdFactory | None = None,
    ) -> "RunIdentity":
        """Create a sanitized user identity and matching run ID.

        Args:
            user_id: Optional user identifier
                (uses anonymous fallback if None).
            scope: ID scope string (default 'run').
            id_factory: Optional IdFactory instance (creates new one if None).

        Returns:
            RunIdentity: New identity with user_id, run_id, and created_at.
        """
        factory = id_factory or IdFactory()
        created_at = factory.current_utc()
        safe_user_id = resolve_user_id(user_id)
        return cls(
            user_id=safe_user_id,
            run_id=factory.run_id(scope, safe_user_id, current=created_at),
            created_at=created_at,
        )

    @property
    def date_stamp(self) -> str:
        """Return the UTC date stamp for this run.

        Returns:
            str: Date stamp in format YYYYMMDD.
        """
        return self.created_at.strftime("%Y%m%d")

    def scoped_id(self, kind: str, *parts: object) -> str:
        """Return a readable ID scoped under this run.

        Args:
            kind: ID kind/scope (e.g., 'task', 'output').
            *parts: Optional path-safe string parts for readability.

        Returns:
            str: Scoped ID string in format kind-parts-run_id.
        """
        safe_parts = [safe_path_segment(kind, "id")]
        safe_parts.extend(
            safe_path_segment(part, "part")
            for part in parts
            if str(part or "").strip()
        )
        return "-".join([*safe_parts, self.run_id])


def resolve_user_id(user_id: str | None) -> str:
    """Return a safe user ID, falling back to the shared anonymous user.

    In relay mode a configured ``RELAY_USER_ID`` (the operator-assigned
    tenant id) overrides the passed user id, because the server-side OBS
    relay confines every object key to ``agent_data/{user_data,uploads}/
    <key.user_id>/`` and the child must produce keys under that same
    tenant segment. The env is read directly (like ``relay_mode_enabled``)
    to avoid building a ``ServerConfig`` in this leaf path helper.

    Args:
        user_id: Raw user ID string (None or empty uses 'anonymous').

    Returns:
        str: Sanitized user ID string safe for path usage.
    """
    if relay_mode_enabled():
        relay_user = os.environ.get(
            "PHYTOMNI_RELAY_USER_ID"
        ) or os.environ.get("RELAY_USER_ID")
        if relay_user:
            return safe_path_segment(relay_user, DEFAULT_USER_ID)
    return safe_path_segment(user_id or DEFAULT_USER_ID, DEFAULT_USER_ID)


def safe_path_segment(value: object, fallback: str) -> str:
    """Return one path-safe segment or raise on traversal-like input.

    Args:
        value: Object to convert to path-safe string.
        fallback: Fallback string if value is empty or None.

    Returns:
        str: Path-safe segment string.

    Raises:
        PathPolicyError: If value or fallback contains path
            separators or traversal.
    """
    raw_value = str(value or "").strip()
    candidate = raw_value or fallback
    if _contains_path_separator(candidate) or candidate in {".", ".."}:
        raise PathPolicyError(f"Unsafe path segment: {value!r}")

    safe_value = _SAFE_SEGMENT_PATTERN.sub("-", candidate).strip("._-")
    if safe_value:
        return safe_value
    if fallback in {".", ".."} or _contains_path_separator(fallback):
        raise PathPolicyError(f"Unsafe fallback segment: {fallback!r}")
    return _SAFE_SEGMENT_PATTERN.sub("-", fallback).strip("._-")


def run_root_key(identity: RunIdentity) -> str:
    """Return the OBS object-key prefix for one workflow run.

    Args:
        identity: RunIdentity instance with user_id, run_id, date_stamp.

    Returns:
        str: OBS object-key prefix for the run directory.
    """
    return (
        f"{USER_DATA_ROOT}/{identity.user_id}/runs/"
        f"{identity.date_stamp}/{identity.run_id}/"
    )


def task_root_key(identity: RunIdentity, task: str) -> str:
    """Return the OBS object-key prefix for one task in a run.

    Args:
        identity: RunIdentity instance with user_id, run_id, date_stamp.
        task: Task identifier string.

    Returns:
        str: OBS object-key prefix for the task directory.
    """
    return f"{run_root_key(identity)}{safe_path_segment(task, 'task')}/"


def task_output_key(identity: RunIdentity, task: str) -> str:
    """Return the OBS object-key prefix for task outputs.

    Args:
        identity: RunIdentity instance with user_id, run_id, date_stamp.
        task: Task identifier string.

    Returns:
        str: OBS object-key prefix for task output directory.
    """
    return f"{task_root_key(identity, task)}output/"


def shared_output_key(fingerprint: str) -> str:
    """Return the content-addressed OBS output prefix for a fingerprint.

    The path carries no ``user_id`` so an identical analysis dedupes
    across tenants without leaking the prior submitter's namespace. Read
    access is gated at the relay by possession of the fingerprint (it is
    a sha256 of the canonical inputs, so it is unguessable).

    Args:
        fingerprint: ``analyst_task_fingerprint`` hex digest.

    Returns:
        str: ``agent_data/shared/<fingerprint>/output/`` object-key prefix.
    """
    return f"{AGENT_DATA_ROOT}/shared/{fingerprint}/output/"


def task_tmp_key(
    identity: RunIdentity,
    task: str,
    object_name: str | None = None,
) -> str:
    """Return a task temporary prefix, optionally with a safe object name.

    Args:
        identity: RunIdentity instance with user_id, run_id, date_stamp.
        task: Task identifier string.
        object_name: Optional object name to append to the prefix.

    Returns:
        str: OBS object-key prefix for task temporary directory or object.
    """
    tmp_key = f"{task_root_key(identity, task)}tmp/"
    if object_name is None:
        return tmp_key
    return f"{tmp_key}{safe_path_segment(object_name, 'object')}"


def task_downloads_key(identity: RunIdentity, task: str) -> str:
    """Return the OBS object-key prefix for per-run downloaded artifacts.

    Args:
        identity: RunIdentity instance with user_id, run_id, date_stamp.
        task: Task identifier string.

    Returns:
        str: OBS object-key prefix for task download directory.
    """
    return f"{task_root_key(identity, task)}downloads/"


def _contains_path_separator(value: str) -> bool:
    """Return whether a value contains path separators."""
    return "/" in value or "\\" in value
