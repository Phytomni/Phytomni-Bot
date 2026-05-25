# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cache lifecycle: path resolution, atexit hooks, and metadata sync.

Helpers called once per decorated function during CacheRuntime init.
Handles environment-backed DB path resolution, atexit storage close
registration, and cache metadata change detection with auto-clear.
"""

import atexit
import logging
import os
from pathlib import Path
from typing import Any

from .exceptions import CacheError
from .storage import Storage

__all__ = [
    "_check_and_update_meta",
    "_register_storage_close",
    "_resolve_db_path",
    "default_cache_db_path",
]

logger = logging.getLogger(__name__)

_atexit_registered: set[str] = set()

DEFAULT_CACHE_DB_ENV = "PHYTOMNI_CACHE_DB"
DEFAULT_CACHE_DB_PATH = Path(".cache") / "phytomni" / "func_cache.sqlite"


def _log_warning(message: str) -> None:
    """Log a preformatted warning message."""
    logger.warning(message)


def default_cache_db_path() -> str:
    """Return the default SQLite path for function result cache storage.

    Returns:
        Environment-provided cache path, or the repository default path.
    """
    env_path = os.getenv(DEFAULT_CACHE_DB_ENV)
    if env_path:
        return env_path
    return str(DEFAULT_CACHE_DB_PATH)


def _resolve_db_path(db_path: Any) -> str:
    """Resolve an explicit or environment-backed cache database path."""
    if db_path is not None:
        return os.fspath(db_path)
    return default_cache_db_path()


def _register_storage_close(db_path: str, storage: Storage) -> None:
    """Register one storage close hook per absolute database path."""
    db_abs = str(Path(db_path).resolve())
    if db_abs in _atexit_registered:
        return
    atexit.register(storage.close)
    _atexit_registered.add(db_abs)


def _check_and_update_meta(
    storage: Storage,
    func_id: str,
    key_params: Any,
    compress: bool,
) -> None:
    """Check and update cache metadata, clearing cache on config changes."""
    try:
        meta = storage.get_meta(func_id)
        if meta is None:
            storage.set_meta(func_id, key_params, compress)
            return

        old_params, old_compress = meta
        if old_params == key_params and old_compress == compress:
            return

        _clear_changed_cache(
            storage,
            func_id,
            _cache_config_changes(
                old_params,
                key_params,
                old_compress,
                compress,
            ),
            key_params,
            compress,
        )
    except CacheError as exc:
        _log_warning(f"Metadata check failed: {exc}")


def _cache_config_changes(
    old_params: Any,
    key_params: Any,
    old_compress: bool,
    compress: bool,
) -> list[str]:
    """Return readable cache metadata changes."""
    changes: list[str] = []
    if old_params != key_params:
        changes.append(f"key_params: {old_params} -> {key_params}")
    if old_compress != compress:
        changes.append(f"compress: {old_compress} -> {compress}")
    return changes


def _clear_changed_cache(
    storage: Storage,
    func_id: str,
    changes: list[str],
    key_params: Any,
    compress: bool,
) -> None:
    """Clear cache entries after metadata changes and persist new metadata."""
    changes_text = "; ".join(changes)
    _log_warning(
        f"Detected cache config change for {func_id}: {changes_text}, "
        "automatically clearing old cache",
    )
    storage.delete_func(func_id)
    storage.cleanup_func_locks(func_id)
    storage.set_meta(func_id, key_params, compress)
