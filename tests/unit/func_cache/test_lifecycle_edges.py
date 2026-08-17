# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for func_cache lifecycle helpers."""

# pylint: disable=protected-access

from __future__ import annotations

from typing import cast

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

from mcp_server_phytomni.func_cache import lifecycle
from mcp_server_phytomni.func_cache.exceptions import CacheError
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit

_LIFECYCLE_LOGGER = "mcp_server_phytomni.func_cache.lifecycle"


class _RecordingStorage:
    """In-memory storage stub that records metadata mutations."""

    def __init__(self, meta: tuple[object, bool] | None) -> None:
        self.meta = meta
        self.calls: list[tuple[str, object]] = []

    def get_meta(self, _func_id: str) -> tuple[object, bool] | None:
        """Return the configured metadata snapshot."""
        return self.meta

    def set_meta(
        self, func_id: str, key_params: object, compress: bool
    ) -> None:
        """Record a metadata write."""
        self.calls.append(("set", (func_id, key_params, compress)))

    def delete_func(self, func_id: str) -> None:
        """Record a function-cache delete."""
        self.calls.append(("delete", func_id))

    def cleanup_func_locks(self, func_id: str) -> None:
        """Record lock cleanup after a metadata change."""
        self.calls.append(("locks", func_id))


def test_log_warning_emits_logger_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lifecycle warning helper writes through the module logger."""
    with capture_non_propagating_logger(_LIFECYCLE_LOGGER, caplog.handler):
        lifecycle._log_warning("cache-lifecycle-warning")
    assert "cache-lifecycle-warning" in caplog.text


def test_default_cache_db_path_uses_repository_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an env override the repository default path is used."""
    monkeypatch.delenv(lifecycle.DEFAULT_CACHE_DB_ENV, raising=False)
    expected = str(lifecycle.DEFAULT_CACHE_DB_PATH)
    assert lifecycle.default_cache_db_path() == expected
    assert lifecycle._resolve_db_path(None) == expected
    assert lifecycle._resolve_db_path("/explicit.sqlite") == (
        "/explicit.sqlite"
    )


def test_check_and_update_meta_writes_when_missing() -> None:
    """A first metadata write does not clear anything."""
    store = _RecordingStorage(None)
    lifecycle._check_and_update_meta(cast(Storage, store), "fn", ("a",), False)
    assert store.calls == [("set", ("fn", ("a",), False))]


def test_check_and_update_meta_is_noop_when_unchanged() -> None:
    """Matching key params and compress leave the cache alone."""
    store = _RecordingStorage((("a",), False))
    lifecycle._check_and_update_meta(cast(Storage, store), "fn", ("a",), False)
    assert store.calls == []


@pytest.mark.parametrize(
    ("old_meta", "new_params", "new_compress", "expected_change"),
    [
        ((("old",), False), ("new",), False, "key_params:"),
        ((("a",), False), ("a",), True, "compress:"),
        ((("old",), False), ("new",), True, "key_params:"),
    ],
)
def test_check_and_update_meta_clears_on_config_change(
    old_meta: tuple[object, bool],
    new_params: object,
    new_compress: bool,
    expected_change: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any metadata drift clears entries, locks, and rewrites meta."""
    store = _RecordingStorage(old_meta)
    with capture_non_propagating_logger(_LIFECYCLE_LOGGER, caplog.handler):
        lifecycle._check_and_update_meta(
            cast(Storage, store), "fn", new_params, new_compress
        )
    assert ("delete", "fn") in store.calls
    assert ("locks", "fn") in store.calls
    assert ("set", ("fn", new_params, new_compress)) in store.calls
    assert "Detected cache config change for fn" in caplog.text
    assert expected_change in caplog.text


def test_check_and_update_meta_logs_cache_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A storage CacheError is a warning, not a raised failure."""

    class _BoomStorage:
        def get_meta(self, _func_id: str) -> None:
            """Raise the storage error the helper must swallow."""
            raise CacheError("meta unavailable")

        def close(self) -> None:
            """Satisfy the pylint public-method floor for this stub."""

    with capture_non_propagating_logger(_LIFECYCLE_LOGGER, caplog.handler):
        lifecycle._check_and_update_meta(
            cast(Storage, _BoomStorage()), "fn", (), False
        )
    assert "Metadata check failed: meta unavailable" in caplog.text


def test_cache_config_changes_lists_each_drift() -> None:
    """Both key-param and compress deltas are reported."""
    assert lifecycle._cache_config_changes("old", "new", False, True) == [
        "key_params: old -> new",
        "compress: False -> True",
    ]
    assert lifecycle._cache_config_changes("same", "same", True, True) == []


def test_clear_changed_cache_persists_replacement_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The clear helper logs, deletes, unlocks, then writes new meta."""
    store = _RecordingStorage(("old", False))
    with capture_non_propagating_logger(_LIFECYCLE_LOGGER, caplog.handler):
        lifecycle._clear_changed_cache(
            cast(Storage, store),
            "fn",
            ["key_params: old -> new"],
            ("new",),
            True,
        )
    assert "automatically clearing old cache" in caplog.text
    assert store.calls == [
        ("delete", "fn"),
        ("locks", "fn"),
        ("set", ("fn", ("new",), True)),
    ]
