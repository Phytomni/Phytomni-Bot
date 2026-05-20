# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Admin operations over the func_cache SQLite store.

Functions: cache_stats, purge_all, purge_func, reexpire_all,
reexpire_func, purge_expired_entries.
"""

import time

from .decorator import default_cache_db_path
from .storage import Storage


def _resolve_storage(db_path):
    """Return the Storage singleton for ``db_path`` or the default.

    Args:
        db_path: Explicit SQLite path for the cache store, or None to
            resolve via the PHYTOMNI_CACHE_DB environment variable and
            the repo-default ``.cache/phytomni/func_cache.sqlite``.

    Returns:
        Shared ``Storage`` singleton bound to the resolved path.
    """
    return Storage.get_instance(db_path or default_cache_db_path())


def cache_stats(db_path=None):
    """Return live entry counts per func_id in the cache database.

    Args:
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        Mapping ``{func_id: live_entry_count}`` for every func that
        currently has at least one row in cache_entries. Expired-but-
        not-yet-purged rows are excluded because ``Storage.count``
        already filters them out.
    """
    storage = _resolve_storage(db_path)
    return {
        func_id: storage.count(func_id) for func_id in storage.list_funcs()
    }


def purge_func(func_id, db_path=None):
    """Delete every cache entry and lock for one function.

    Args:
        func_id: Fully qualified cached function identifier.
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        Number of live entries that existed for the function before
        deletion. Pre-counted via ``Storage.count`` so the returned
        figure matches what ``cache_stats`` would report immediately
        before this call.
    """
    storage = _resolve_storage(db_path)
    purged = storage.count(func_id)
    storage.delete_func(func_id)
    storage.cleanup_func_locks(func_id)
    return purged


def purge_all(db_path=None):
    """Delete every cache entry and lock in the database.

    Args:
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        Total number of live entries purged across all funcs (the sum
        of ``Storage.count`` over ``Storage.list_funcs``).
    """
    storage = _resolve_storage(db_path)
    total = 0
    for func_id in storage.list_funcs():
        total += storage.count(func_id)
        storage.delete_func(func_id)
        storage.cleanup_func_locks(func_id)
    return total


def reexpire_all(new_ttl, db_path=None):
    """Rewrite expires_at on every cache entry.

    Args:
        new_ttl: New time-to-live in seconds, applied as
            ``time.time() + new_ttl``. Pass ``None`` to drop the TTL
            entirely so matched rows become permanent.
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        Number of rows updated, as reported by SQLite ``rowcount``.
    """
    storage = _resolve_storage(db_path)
    new_at = time.time() + new_ttl if new_ttl is not None else None
    return storage.reexpire(None, new_at)


def reexpire_func(func_id, new_ttl, db_path=None):
    """Rewrite expires_at on one function's cache entries.

    Args:
        func_id: Fully qualified cached function identifier.
        new_ttl: New time-to-live in seconds, applied as
            ``time.time() + new_ttl``. Pass ``None`` to drop the TTL.
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        Number of rows updated, as reported by SQLite ``rowcount``.
    """
    storage = _resolve_storage(db_path)
    new_at = time.time() + new_ttl if new_ttl is not None else None
    return storage.reexpire(func_id, new_at)


def purge_expired_entries(db_path=None):
    """Delete every already-expired cache entry.

    Args:
        db_path: Optional explicit SQLite path; see ``_resolve_storage``.

    Returns:
        None. The underlying ``Storage.purge_expired`` does not report
        a rowcount, so callers should follow up with ``cache_stats``
        if they need a precise post-purge view.
    """
    storage = _resolve_storage(db_path)
    storage.purge_expired()
