# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Function-cache metadata and path lifecycle helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.func_cache import lifecycle
from mcp_server_phytomni.func_cache.exceptions import CacheError

pytestmark = pytest.mark.unit


def test_default_cache_db_path_prefers_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit cache path env var wins over the repository default."""
    monkeypatch.setenv(
        lifecycle.DEFAULT_CACHE_DB_ENV, "/tmp/custom-cache.sqlite"
    )
    assert lifecycle.default_cache_db_path() == "/tmp/custom-cache.sqlite"
    assert lifecycle._resolve_db_path(None) == "/tmp/custom-cache.sqlite"
    assert lifecycle._resolve_db_path("/explicit.sqlite") == "/explicit.sqlite"


def test_register_storage_close_is_idempotent_per_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One atexit hook is registered for each absolute cache database."""
    registered: list[object] = []
    monkeypatch.setattr(lifecycle.atexit, "register", registered.append)
    lifecycle._atexit_registered.clear()
    storage = SimpleNamespace(close=object())
    path = str(tmp_path / "cache.sqlite")
    lifecycle._register_storage_close(path, storage)
    lifecycle._register_storage_close(path, storage)
    assert registered == [storage.close]


def test_check_and_update_meta_clears_on_config_change() -> None:
    """Changed key params or compress flags drop the old function cache."""
    calls: list[tuple[str, object]] = []

    class _Storage:
        def get_meta(self, func_id: str) -> tuple[object, bool]:
            return ("old", False)

        def set_meta(
            self, func_id: str, key_params: object, compress: bool
        ) -> None:
            calls.append(("set", (func_id, key_params, compress)))

        def delete_func(self, func_id: str) -> None:
            calls.append(("delete", func_id))

        def cleanup_func_locks(self, func_id: str) -> None:
            calls.append(("locks", func_id))

    lifecycle._check_and_update_meta(_Storage(), "fn", "new", True)
    assert ("delete", "fn") in calls
    assert ("locks", "fn") in calls
    assert ("set", ("fn", "new", True)) in calls


def test_check_and_update_meta_logs_cache_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A storage CacheError is a warning, not a raised failure."""
    warnings: list[str] = []
    monkeypatch.setattr(lifecycle, "_log_warning", warnings.append)

    class _Storage:
        def get_meta(self, func_id: str) -> None:
            raise CacheError("meta unavailable")

    lifecycle._check_and_update_meta(_Storage(), "fn", (), False)
    assert warnings and "meta unavailable" in warnings[0]
