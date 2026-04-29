import sqlite3
import threading
import time
import json
import os
import logging

from .exceptions import StorageError

logger = logging.getLogger(__name__)


class Storage:

    _instances: dict[str, "Storage"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def get_instance(cls, db_path):
        db_path = os.path.abspath(db_path)
        with cls._instances_lock:
            if db_path not in cls._instances:
                cls._instances[db_path] = cls(db_path)
            return cls._instances[db_path]

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._pid = os.getpid()
        self._init_db()

    def _get_conn(self):
        current_pid = os.getpid()
        if current_pid != self._pid:
            self._pid = current_pid
            self._local = threading.local()

        if not hasattr(self._local, "conn") or self._local.conn is None:
            try:
                conn = sqlite3.connect(
                    self.db_path, timeout=10, isolation_level=None
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-8000")
                conn.execute("PRAGMA busy_timeout=5000")
                self._local.conn = conn
            except sqlite3.Error as e:
                raise StorageError(f"Failed to connect to SQLite: {e}") from e
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_entries ("
                "  func_id    TEXT NOT NULL,"
                "  key_hash   TEXT NOT NULL,"
                "  value      BLOB NOT NULL,"
                "  expires_at REAL,"
                "  PRIMARY KEY (func_id, key_hash)"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_locks ("
                "  func_id   TEXT NOT NULL,"
                "  key_hash  TEXT NOT NULL,"
                "  owner     TEXT NOT NULL,"
                "  locked_at REAL NOT NULL,"
                "  PRIMARY KEY (func_id, key_hash)"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_meta ("
                "  func_id    TEXT PRIMARY KEY,"
                "  key_params TEXT NOT NULL,"
                "  compress   INTEGER NOT NULL DEFAULT 0,"
                "  created_at REAL NOT NULL"
                ")"
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to initialize database: {e}") from e

    # ────────── cache_entries ────────── #

    def get(self, func_id, key_hash):
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT value, expires_at FROM cache_entries "
                "WHERE func_id=? AND key_hash=?",
                (func_id, key_hash),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at is not None and expires_at < time.time():
                conn.execute(
                    "DELETE FROM cache_entries "
                    "WHERE func_id=? AND key_hash=?",
                    (func_id, key_hash),
                )
                return None
            return value
        except sqlite3.Error as e:
            raise StorageError(f"Failed to read cache: {e}") from e

    def set(self, func_id, key_hash, value, ttl=None):
        try:
            conn = self._get_conn()
            expires_at = time.time() + ttl if ttl is not None else None
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(func_id, key_hash, value, expires_at) VALUES (?,?,?,?)",
                (func_id, key_hash, value, expires_at),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to write cache: {e}") from e

    def delete_entry(self, func_id, key_hash):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_entries WHERE func_id=? AND key_hash=?",
                (func_id, key_hash),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to delete cache entry: {e}") from e

    def delete_func(self, func_id):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_entries WHERE func_id=?",
                (func_id,),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to delete function cache: {e}") from e

    def count(self, func_id):
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT COUNT(*) FROM cache_entries "
                "WHERE func_id=? "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (func_id, time.time()),
            )
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            raise StorageError(f"Failed to count cache entries: {e}") from e

    def purge_expired(self):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_entries "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to purge expired cache: {e}") from e

    # ────────── cache_meta ────────── #

    def get_meta(self, func_id):
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT key_params, compress FROM cache_meta "
                "WHERE func_id=?",
                (func_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return json.loads(row[0]), bool(row[1])
        except sqlite3.Error as e:
            raise StorageError(f"Failed to read metadata: {e}") from e

    def set_meta(self, func_id, key_params, compress):
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta "
                "(func_id, key_params, compress, created_at) "
                "VALUES (?,?,?,?)",
                (func_id, json.dumps(key_params), int(compress), time.time()),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to write metadata: {e}") from e

    # ────────── cache_locks ────────── #

    def try_acquire_lock(self, func_id, key_hash, owner, lock_expire):
        conn = self._get_conn()
        now = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM cache_locks "
                "WHERE func_id=? AND key_hash=? AND locked_at<?",
                (func_id, key_hash, now - lock_expire),
            )
            conn.execute(
                "INSERT OR IGNORE INTO cache_locks "
                "(func_id, key_hash, owner, locked_at) VALUES (?,?,?,?)",
                (func_id, key_hash, owner, now),
            )
            cursor = conn.execute(
                "SELECT owner FROM cache_locks "
                "WHERE func_id=? AND key_hash=?",
                (func_id, key_hash),
            )
            row = cursor.fetchone()
            conn.execute("COMMIT")
            return row is not None and row[0] == owner
        except sqlite3.Error as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None
            raise StorageError(f"Failed to acquire lock: {e}") from e

    def release_lock(self, func_id, key_hash, owner):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_locks "
                "WHERE func_id=? AND key_hash=? AND owner=?",
                (func_id, key_hash, owner),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to release lock: {e}") from e

    def cleanup_process_locks(self, pid):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_locks WHERE owner LIKE ?",
                (f"{pid}:%",),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to cleanup process locks: {e}") from e

    def cleanup_func_locks(self, func_id):
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM cache_locks WHERE func_id=?",
                (func_id,),
            )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to cleanup function locks: {e}") from e

    def close(self):
        try:
            self.cleanup_process_locks(os.getpid())
        except Exception:
            pass
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
