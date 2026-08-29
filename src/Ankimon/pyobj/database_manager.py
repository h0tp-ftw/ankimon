"""
AnkimonDB - Consolidated Database Manager for Ankimon

This module provides a SQLite-based storage solution for all Ankimon game data,
replacing multiple JSON files with a single, obfuscated database file.
"""

import json
import sqlite3
import threading
import uuid
import weakref
import gc
import time
import contextlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import csv
from ..resources import user_path, csv_file_items_cost, mypokemon_path, mainpokemon_path, items_path, badges_path, team_pokemon_path as team_path


# --- Thread-safe connection layer (Stage A scaffolding) --------------------
#
# Re-fit from BRRRR_Experimental: a thin ConnectionWrapper plus per-thread
# connections let the async-boot path and (deferred) mobile-review engine touch
# the DB off the GUI thread without tripping sqlite's default same-thread guard.
# Kept aqt-free (the thread check imports PyQt6 lazily and degrades to "main
# thread" when there is no QApplication). Backward compatible: on the GUI thread
# callers get the same single shared connection they always did, now WAL-mode.

class CursorWrapper:
    """Wraps a sqlite3.Cursor, proxying everything, holding a connection lease
    during its lifetime to prevent connection closure during in-flight operations."""

    def __init__(self, cursor, conn_wrapper, *, lease_acquired=False):
        self._cursor = cursor
        self._conn_wrapper = conn_wrapper
        self._lease_released = False
        if not lease_acquired:
            self._conn_wrapper.acquire_lease()

    def release_lease(self):
        if not self._lease_released:
            self._lease_released = True
            try:
                self._conn_wrapper.release_lease()
            except Exception:
                pass

    def __del__(self):
        self.release_lease()

    def close(self):
        try:
            self._cursor.close()
        finally:
            self.release_lease()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def execute(self, *args, **kwargs):
        self._cursor.execute(*args, **kwargs)
        return self

    def executemany(self, *args, **kwargs):
        self._cursor.executemany(*args, **kwargs)
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ConnectionWrapper:
    """Wraps a sqlite3.Connection, proxying everything, with a re-entrant
    ``with conn:`` transaction and an opt-out commit flag (used by bulk paths)."""

    def __init__(self, conn, db_mgr=None):
        self._conn = conn
        self._disable_commit = False
        self._db_mgr = db_mgr
        self._lease_lock = threading.Lock()
        self._lease_count = 0
        self._close_pending = False
        self._closed = False

    def acquire_lease(self):
        with self._lease_lock:
            self._lease_count += 1

    def release_lease(self):
        with self._lease_lock:
            self._lease_count = max(0, self._lease_count - 1)
            if self._lease_count == 0 and self._close_pending and not self._closed:
                try:
                    self._conn.close()
                    self._closed = True
                except Exception:
                    pass

    def cancel_close(self):
        """Cancel a deferred close after the manager's drain attempt times out."""
        with self._lease_lock:
            if not self._closed:
                self._close_pending = False

    def commit(self):
        # NOTE: Direct raw cursor execution (conn.cursor().execute(...)) bypasses wrapper-level auto-healing.
        self.acquire_lease()
        lease_released = False
        try:
            if not self._disable_commit:
                try:
                    self._conn.commit()
                except sqlite3.DatabaseError as e:
                    msg = str(e).lower()
                    # NOTE: Deliberately NO "closed database" retry here. Unlike
                    # execute()/executemany(), commit() has nothing to replay:
                    # sqlite rolled this connection's transaction back when it was
                    # closed, so committing a *different* connection would be a
                    # no-op that reports success for a write that no longer exists.
                    # Let it raise -- see the __exit__ comment in database_manager.
                    if self._db_mgr and ("malformed" in msg or "disk image" in msg) and not self._db_mgr._is_repairing:
                        self.release_lease()
                        lease_released = True
                        self._db_mgr.repair_database()
                        fresh = self._db_mgr._get_connection()
                        fresh._conn.commit()
                        return
                    raise
        finally:
            if not lease_released:
                self.release_lease()

    def rollback(self):
        self.acquire_lease()
        try:
            self._conn.rollback()
        finally:
            self.release_lease()

    def close(self, wait_seconds: float = 0.0) -> bool:
        """Close when leases drain; return ``False`` if the deadline expires."""
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            with self._lease_lock:
                self._close_pending = True
                if self._closed:
                    return True
                if self._lease_count == 0:
                    try:
                        self._conn.close()
                        self._closed = True
                        return True
                    except Exception:
                        return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def execute(self, *args, **kwargs):
        # NOTE: Direct raw cursor execution (conn.cursor().execute(...)) bypasses wrapper-level auto-healing.
        self.acquire_lease()
        lease_released = False
        try:
            res = self._conn.execute(*args, **kwargs)
            return CursorWrapper(res, self)
        except sqlite3.DatabaseError as e:
            msg = str(e).lower()
            if self._db_mgr and isinstance(e, sqlite3.ProgrammingError) and "closed database" in msg:
                self.release_lease()
                lease_released = True
                fresh = self._db_mgr._get_connection()
                return fresh.execute(*args, **kwargs)
            if self._db_mgr and ("malformed" in msg or "disk image" in msg) and not self._db_mgr._is_repairing:
                self.release_lease()
                lease_released = True
                self._db_mgr.repair_database()
                fresh = self._db_mgr._get_connection()
                return fresh.execute(*args, **kwargs)
            raise
        finally:
            if not lease_released:
                self.release_lease()

    def executemany(self, *args, **kwargs):
        # NOTE: Direct raw cursor execution (conn.cursor().execute(...)) bypasses wrapper-level auto-healing.
        self.acquire_lease()
        lease_released = False
        try:
            res = self._conn.executemany(*args, **kwargs)
            return CursorWrapper(res, self)
        except sqlite3.DatabaseError as e:
            msg = str(e).lower()
            if self._db_mgr and isinstance(e, sqlite3.ProgrammingError) and "closed database" in msg:
                self.release_lease()
                lease_released = True
                fresh = self._db_mgr._get_connection()
                return fresh.executemany(*args, **kwargs)
            if self._db_mgr and ("malformed" in msg or "disk image" in msg) and not self._db_mgr._is_repairing:
                self.release_lease()
                lease_released = True
                self._db_mgr.repair_database()
                fresh = self._db_mgr._get_connection()
                return fresh.executemany(*args, **kwargs)
            raise
        finally:
            if not lease_released:
                self.release_lease()

    def cursor(self, *args, **kwargs):
        self.acquire_lease()
        try:
            raw_cursor = self._conn.cursor(*args, **kwargs)
            return CursorWrapper(raw_cursor, self, lease_acquired=True)
        except sqlite3.DatabaseError as e:
            self.release_lease()
            msg = str(e).lower()
            if self._db_mgr and isinstance(e, sqlite3.ProgrammingError) and "closed database" in msg:
                fresh = self._db_mgr._get_connection()
                return fresh.cursor(*args, **kwargs)
            if self._db_mgr and ("malformed" in msg or "disk image" in msg) and not self._db_mgr._is_repairing:
                self._db_mgr.repair_database()
                fresh = self._db_mgr._get_connection()
                return fresh.cursor(*args, **kwargs)
            raise
        except Exception:
            self.release_lease()
            raise

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        self.acquire_lease()
        try:
            if getattr(self, "_txn_depth", 0) == 0:
                self._conn.execute("BEGIN")
            self._txn_depth = getattr(self, "_txn_depth", 0) + 1
            return self
        except Exception:
            self.release_lease()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._txn_depth = getattr(self, "_txn_depth", 1) - 1
            if self._txn_depth == 0:
                if exc_type is not None:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                else:
                    # Do NOT swallow a commit failure: eating it here makes a lost
                    # write (disk full, or the busy_timeout expiring under write
                    # contention) look like success, so callers such as
                    # queue_mobile_battles / add_mobile_history_entries_batch /
                    # set_mobile_watermark return their success flag while nothing
                    # was persisted. Roll back and let the error propagate so the
                    # caller's error path (and the user) actually see it.
                    try:
                        self._conn.commit()
                    except Exception:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
            return False
        finally:
            self.release_lease()



def _is_main_thread() -> bool:
    """True on Qt's GUI thread, or whenever Qt is not loaded (headless / the
    Tier-1 no-Qt harness / tests).

    Deliberately consults ``sys.modules`` instead of importing PyQt6: force-loading
    the Qt libraries inside the aqt-free Tier-1 harness (where PyQt6 may be
    *installed* but must never be *initialized*) crashes with 'Qt requires a
    QCoreApplication'. If Qt has not been loaded, we are headless → treat as main
    thread. In real Anki, Qt is always already loaded, so the real check runs."""
    import sys
    qtwidgets = sys.modules.get("PyQt6.QtWidgets")
    qtcore = sys.modules.get("PyQt6.QtCore")
    if qtwidgets is None or qtcore is None:
        return True
    try:
        app = qtwidgets.QApplication.instance()
        if not app:
            return True
        return qtcore.QThread.currentThread() == app.thread()
    except Exception:
        return True


class AnkimonDB:
    """Handles all database operations for Ankimon. Stores data in SQLite."""
    
    DB_FILENAME = "ankimon.db"

    # Every connection (GUI + per-background-thread) waits this long for a
    # write lock before sqlite raises "database is locked". See _prepare_connection.
    _BUSY_TIMEOUT_MS = 30000

    def __init__(self, logger=None, db_path: Optional[Union[str, Path]] = None,
                 *, wal: bool = False):
        self.logger = logger
        # db_path override supports multi-profile / account switching (switch_database).
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = user_path / self.DB_FILENAME
        # WAL is OPT-IN (default off) to preserve main's single-file persistence /
        # backup guard: probe_persistence and BackupManager copy just ``ankimon.db``,
        # which would miss WAL's ``-wal`` sidecar. A deferred concurrent-writer leaf
        # (mobile-sync) turns WAL on together with a checkpoint-before-copy backup fix.
        self._wal = wal
        self._connection: Optional[ConnectionWrapper] = None       # GUI-thread connection
        self._local_conn = threading.local()                       # per-background-thread
        self._all_connections = []
        self._conn_lock = threading.RLock()
        # Serialises the read-modify-write of the pokedex_caught / pokedex_seen
        # user_data lists. save_pokemon() runs on the background mobile-sync
        # thread as well as the GUI thread, and those two lists are rewritten
        # wholesale, so without this an interleaved save loses an id.
        self._pokedex_lock = threading.Lock()
        self._is_repairing = False
        # When non-None, mark_mobile_battle_resolved defers the mirror-DB sync
        # (which commits on a separate connection, escaping any outer transaction)
        # and collects the revlog ids here to be flushed after the caller's bulk
        # transaction commits. See begin/flush/discard_deferred_mirror_sync.
        self._deferred_mirror_revlog_ids: Optional[list] = None
        self._connection_epoch = 0
        self._connection_epoch_gui = -1
        self._setup_database()
        self._reconcile_pokedex_history_safely()

    def _prepare_connection(self, conn):
        """Apply row factory, a generous busy-timeout, and (only when opted in)
        WAL-family PRAGMAs, then wrap."""
        conn.row_factory = sqlite3.Row  # Access columns by name
        # Busy-timeout on EVERY connection (GUI + per-background-thread), regardless
        # of journal mode. mobile-sync's bulk "Resolve All" runs on a background
        # thread and holds one long write transaction (conn._disable_commit while it
        # batches every companion), so a concurrent GUI-thread write — a live
        # review's save_pokemon / set_config_value — can find the DB write-locked.
        # Without a busy-timeout sqlite3 raises "database is locked" immediately;
        # with one it waits for the bulk transaction to commit instead of erroring.
        # 30s comfortably covers a full bulk resolve (Python's connect() default is
        # only 5s). This is the robust, single-file-safe alternative to enabling WAL
        # here (which would need a checkpoint-before-copy backup fix, NR-05).
        try:
            conn.execute(f"PRAGMA busy_timeout={self._BUSY_TIMEOUT_MS};")
        except Exception as e:
            self._log("warning", f"Failed to set busy_timeout: {e}")
        if self._wal:
            try:
                mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                if str(mode).lower() != "wal":
                    conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA temp_store=MEMORY;")
            except Exception as e:
                self._log("warning", f"Failed to set WAL PRAGMAs: {e}")
        wrapper = ConnectionWrapper(conn, self)
        with self._conn_lock:
            self._all_connections.append(weakref.ref(wrapper))
        return wrapper

    def _log(self, level: str, message: str):
        """Helper for logging."""
        if self.logger:
            self.logger.log(level, message)
        else:
            print(f"[{level}] {message}")

    # --- Connection Management ---

    @contextlib.contextmanager
    def lease_connection(self):
        """Lease a connection wrapper, ensuring it won't be closed until the lease is released."""
        conn = self._get_connection()
        conn.acquire_lease()
        try:
            yield conn
        finally:
            conn.release_lease()

    def _get_connection(self) -> ConnectionWrapper:
        """Gets or creates a database connection for the CURRENT thread.

        Connection creation and registration share the lifecycle lock with
        shutdown, so a raw SQLite handle cannot appear between a close snapshot
        and the file transition that follows it.
        """
        with self._conn_lock:
            epoch = self._connection_epoch
            if not _is_main_thread():
                local = self._local_conn
                if (not hasattr(local, "conn") or local.conn is None
                        or getattr(local.conn, "_closed", False)
                        or getattr(local, "db_path", None) != self.db_path
                        or getattr(local, "epoch", -1) != epoch):
                    if getattr(local, "conn", None) is not None:
                        try:
                            local.conn.close()
                        except Exception:
                            pass
                    conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                    local.conn = self._prepare_connection(conn)
                    local.db_path = self.db_path
                    local.epoch = epoch
                elif not isinstance(local.conn, ConnectionWrapper):
                    local.conn = ConnectionWrapper(local.conn, self)
                    local.db_path = self.db_path
                    local.epoch = epoch
                return local.conn

            if (self._connection is None
                    or getattr(self._connection, "_closed", False)
                    or self._connection_epoch_gui != epoch):
                if self._connection:
                    try:
                        self._connection.close()
                    except Exception:
                        pass
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                self._connection = self._prepare_connection(conn)
                self._connection_epoch_gui = epoch
            elif not isinstance(self._connection, ConnectionWrapper):
                self._connection = ConnectionWrapper(self._connection, self)
            return self._connection

    def _close_locked(self, wait_seconds: float) -> bool:
        """Drain and invalidate the current generation with ``_conn_lock`` held."""
        deadline = time.monotonic() + max(0.0, wait_seconds)
        wrappers = []
        seen = set()

        def add_wrapper(conn):
            if conn is not None and id(conn) not in seen:
                seen.add(id(conn))
                wrappers.append(conn)

        add_wrapper(self._connection)
        for conn_ref in self._all_connections:
            add_wrapper(conn_ref())
        if hasattr(self, "_local_conn"):
            add_wrapper(getattr(self._local_conn, "conn", None))

        closed_all = True
        for conn in wrappers:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                closed_all = conn.close(remaining) and closed_all
            except Exception:
                closed_all = False

        if not closed_all:
            for conn in wrappers:
                try:
                    conn.cancel_close()
                except Exception:
                    pass
            return False

        self._connection_epoch += 1
        self._connection = None
        self._all_connections.clear()
        if hasattr(self, "_local_conn"):
            self._local_conn.conn = None
        return True

    def close(self, wait_seconds: float = 0.0) -> bool:
        """Close the current generation within one shared deadline."""
        with self._conn_lock:
            return self._close_locked(wait_seconds)

    @contextlib.contextmanager
    def quiesce(self, wait_seconds: float = 0.0):
        """Hold the lifecycle barrier after draining all registered connections."""
        with self._conn_lock:
            yield self._close_locked(wait_seconds)

    def repair_database(self):
        """Repair a corrupted/malformed database out-of-place."""
        if self._is_repairing:
            return
        self._is_repairing = True
        self._log("warning", "SQLite database corruption detected! Initiating out-of-place self-healing repair...")
        
        try:
            db_file = self.db_path
            tmp_db = db_file.with_name(db_file.name + ".tmp")
            backup_db = db_file.with_name(db_file.name + ".corrupt_backup")
            
            if tmp_db.exists():
                try:
                    tmp_db.unlink()
                except Exception:
                    pass
                    
            # Dump current database safely
            src_conn = sqlite3.connect(str(db_file))
            try:
                lines = list(src_conn.iterdump())
            finally:
                src_conn.close()
            
            # Modify schema to define generated columns as physical columns initially,
            # which allows SQL dump inserts (which contain all values) to succeed.
            # We also disable the unique constraint.
            modified_sql = []
            for line in lines:
                if line.startswith("CREATE INDEX") or line.startswith("CREATE UNIQUE INDEX"):
                    if "captured_pokemon" in line:
                        continue
                if "CREATE TABLE captured_pokemon" in line:
                    line = """CREATE TABLE captured_pokemon (
                        individual_id TEXT,
                        is_main INTEGER DEFAULT 0,
                        data TEXT NOT NULL
                    );"""
                elif line.startswith("INSERT INTO "):
                    line = line.replace("INSERT INTO ", "INSERT OR REPLACE INTO ", 1)
                modified_sql.append(line)
            full_sql = "\n".join(modified_sql)
            
            # Write to temporary file
            dest_conn = sqlite3.connect(str(tmp_db))
            dest_conn.executescript(full_sql)
            
            cursor = dest_conn.cursor()
            
            # Propagate is_main flag to all duplicates of the main Pokémon so the highest-progress survivor inherits it
            cursor.execute("""
                UPDATE captured_pokemon 
                SET is_main = 1 
                WHERE individual_id IN (
                    SELECT individual_id FROM captured_pokemon WHERE is_main = 1
                )
            """)

            # Prune duplicate captured_pokemon rows by highest Level and XP
            cursor.execute("""
                DELETE FROM captured_pokemon 
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid, ROW_NUMBER() OVER (
                            PARTITION BY individual_id 
                            ORDER BY 
                                CAST(json_extract(data, '$.level') AS INTEGER) DESC, 
                                CAST(json_extract(data, '$.xp') AS INTEGER) DESC
                        ) as rn 
                        FROM captured_pokemon
                    ) WHERE rn = 1
                )
            """)
            
            # Clean duplicate keys in pending_mobile_battles if table exists
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pending_mobile_battles'")
                if cursor.fetchone():
                    cursor.execute("""
                        DELETE FROM pending_mobile_battles 
                        WHERE rowid NOT IN (
                            SELECT MIN(rowid) 
                            FROM pending_mobile_battles 
                            GROUP BY id
                        )
                    """)
            except Exception:
                pass
                
            # Restore unique PRIMARY KEY table definition
            cursor.execute("ALTER TABLE captured_pokemon RENAME TO captured_pokemon_old")
            cursor.execute("""
                CREATE TABLE captured_pokemon (
                    individual_id TEXT PRIMARY KEY,
                    is_main INTEGER DEFAULT 0,
                    data TEXT NOT NULL,
                    name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,
                    pokedex_id INTEGER GENERATED ALWAYS AS (json_extract(data, '$.id')) VIRTUAL,
                    shiny BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.shiny')) VIRTUAL,
                    level INTEGER GENERATED ALWAYS AS (json_extract(data, '$.level')) VIRTUAL
                )
            """)
            cursor.execute("""
                INSERT INTO captured_pokemon (individual_id, is_main, data)
                SELECT individual_id, is_main, data FROM captured_pokemon_old
            """)
            cursor.execute("DROP TABLE captured_pokemon_old")
            
            # Recreate secondary indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_name ON captured_pokemon(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_pokedex_id ON captured_pokemon(pokedex_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_shiny ON captured_pokemon(shiny)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_level ON captured_pokemon(level)")

            # Normalize base stats in the temp db during repair
            try:
                from ..functions.pokedex_functions import is_valid_base_stats, search_pokedex
                cursor.execute("SELECT individual_id, data FROM captured_pokemon")
                rows = cursor.fetchall()
                updates = []
                for row in rows:
                    ind_id, obfuscated_data = row
                    pokemon_data = self._deobfuscate(obfuscated_data)
                    if not pokemon_data:
                        continue
                    
                    base_stats = pokemon_data.get("base_stats")
                    if not is_valid_base_stats(base_stats):
                        base_stats = search_pokedex(pokemon_data.get("name", ""), "baseStats") or {}
                        if is_valid_base_stats(base_stats):
                            pokemon_data["base_stats"] = base_stats
                            new_obfuscated = self._obfuscate(pokemon_data)
                            updates.append((new_obfuscated, ind_id))
                
                if updates:
                    cursor.executemany("UPDATE captured_pokemon SET data = ? WHERE individual_id = ?", updates)
            except Exception as e:
                self._log("error", f"Failed to normalize base_stats in repair: {e}")
            
            # Rebuild index and verify
            cursor.execute("REINDEX")
            cursor.execute("PRAGMA integrity_check")
            check_result = cursor.fetchone()[0]
            
            if check_result == "ok":
                dest_conn.commit()
                dest_conn.close()
                
                # Keep connection creation blocked until the repaired file is in
                # place; otherwise a new raw handle can appear after close() but
                # before either rename.
                with self.quiesce(2.0) as closed:
                    if not closed:
                        raise RuntimeError(
                            "Database repair aborted because active operations did not finish"
                        )

                    # Clean up existing corrupted backup
                    if backup_db.exists():
                        try:
                            backup_db.unlink()
                        except Exception:
                            pass
                    try:
                        for attempt in range(3):
                            try:
                                db_file.replace(backup_db)
                                break
                            except Exception as e:
                                if attempt == 2:
                                    self._log("warning", f"Failed to backup corrupt database: {e}")
                                    break
                                time.sleep(0.1)
                                gc.collect()
                    except Exception:
                        pass

                    # Swap in repaired file and clean WAL/SHM sidecars
                    try:
                        for attempt in range(3):
                            try:
                                tmp_db.replace(db_file)
                                break
                            except Exception:
                                if attempt == 2:
                                    raise
                                time.sleep(0.1)
                                gc.collect()
                        for sidecar in ("-wal", "-shm"):
                            sidecar_file = db_file.with_name(db_file.name + sidecar)
                            try:
                                if sidecar_file.exists():
                                    sidecar_file.unlink()
                            except Exception:
                                pass
                        self._log("info", "SQLite self-healing completed successfully! Database index repaired.")
                    except Exception as e:
                        self._log("error", f"Failed to swap repaired database: {e}")
                        if backup_db.exists() and not db_file.exists():
                            try:
                                backup_db.replace(db_file)
                            except Exception:
                                pass
            else:
                dest_conn.rollback()
                dest_conn.close()
                self._log("error", f"Integrity check on repaired DB failed: {check_result}")
                if tmp_db.exists():
                    tmp_db.unlink()
        except Exception as e:
            self._log("error", f"Self-healing database repair failed: {e}")
            raise
        finally:
            self._is_repairing = False
            # Clean up the temporary database file if it was left behind due to a crash/exception
            try:
                tmp_db_path = self.db_path.with_name(self.db_path.name + ".tmp")
                if tmp_db_path.exists():
                    tmp_db_path.unlink()
            except Exception:
                pass

    def switch_database(self, db_filename: str, wait_seconds: float = 2.0) -> bool:
        """Close the active profile and reopen against ``db_filename``.

        Refuse to mutate ``db_path`` while any operation still holds a lease; a
        partial switch can route one transaction's writes across two accounts.
        """
        old_path = self.db_path
        with self.quiesce(wait_seconds) as closed:
            if not closed:
                raise RuntimeError(
                    "Database switch aborted because active operations did not finish"
                )

            self.db_path = user_path / db_filename
            self._connection = None
            try:
                self._setup_database()
            except Exception:
                self.db_path = old_path
                self._connection = None
                try:
                    self._setup_database()
                except Exception as restore_error:
                    self._log(
                        "error",
                        f"Failed to restore previous database after switch error: {restore_error}",
                    )
                raise

            self._log("info", f"Switched database to {db_filename}")

        # Deliberately outside ``quiesce``: that holds ``_conn_lock`` for the whole
        # block, and the sweep takes ``_pokedex_lock`` and *then* a connection
        # (i.e. ``_conn_lock``). Running it in there would invert the order every
        # mark_as_caught uses and could deadlock against a concurrent save.
        self._reconcile_pokedex_history_safely()
        return True

    # --- Obfuscation / De-obfuscation ---

    def _obfuscate(self, data: Any) -> str:
        """Serializes a Python object to a JSON string. (Formerly obfuscated)"""
        return json.dumps(data, ensure_ascii=False)

    def _deobfuscate(self, data_str: str) -> Optional[Any]:
        """Deserializes a JSON string to a Python object. (Formerly deobfuscated)"""
        if not data_str:
            return None
        try:
            return json.loads(data_str)
        except Exception as e:
            self._log("error", f"Failed to load json data: {e}")
            return None

    # --- Database Setup ---

    def _setup_database(self):
        """Creates all necessary tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Table for captured pokemon (replaces mypokemon.json AND mainpokemon.json)
        # is_main flag: 0 = not main, 1 = main pokemon
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS captured_pokemon (
                individual_id TEXT PRIMARY KEY,
                is_main INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,
                pokedex_id INTEGER GENERATED ALWAYS AS (json_extract(data, '$.id')) VIRTUAL,
                shiny BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.shiny')) VIRTUAL,
                level INTEGER GENERATED ALWAYS AS (json_extract(data, '$.level')) VIRTUAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_name ON captured_pokemon(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_pokedex_id ON captured_pokemon(pokedex_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_shiny ON captured_pokemon(shiny)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_level ON captured_pokemon(level)")

        # Check if is_main column exists (for migration from old schema)
        cursor.execute("PRAGMA table_info(captured_pokemon)")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_main" not in columns:
            self._log("info", "Migrating schema: adding is_main column...")
            cursor.execute("ALTER TABLE captured_pokemon ADD COLUMN is_main INTEGER DEFAULT 0")
            # Migrate data from old main_pokemon table if it exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='main_pokemon'")
            if cursor.fetchone():
                cursor.execute("SELECT individual_id, data FROM main_pokemon WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    main_id = row[0]
                    main_data = row[1]
                    # Update the existing pokemon to be main, or insert if not exists
                    cursor.execute(
                        "INSERT OR REPLACE INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 1, ?)",
                        (main_id, main_data)
                    )
                cursor.execute("DROP TABLE main_pokemon")
                self._log("info", "Migrated main_pokemon table to is_main flag")

        # Table for items (replaces items.json) - using PokeAPI integer ID as PK
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                item_name TEXT UNIQUE,
                quantity INTEGER DEFAULT 0,
                data TEXT,
                category_id INTEGER,
                cost INTEGER,
                fling_power INTEGER,
                fling_effect_id INTEGER
            )
        """)

        # Table for badges (replaces badges.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS badges (
                badge_id TEXT PRIMARY KEY,
                achieved BOOLEAN DEFAULT 0
            )
        """)

        # Metadata table for tracking migration status, etc.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Table for team composition (replaces team.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team (
                slot_position INTEGER PRIMARY KEY,
                individual_id TEXT NOT NULL
            )
        """)

        # Table for released pokemon history (replaces pokemon_history.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pokemon_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                individual_id TEXT UNIQUE,
                data TEXT NOT NULL
            )
        """)

        # Table for user data/credentials (replaces data.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_data (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Table for config settings (replaces config.obf)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # --- Mobile-review scaffolding tables (Stage A) ---------------------
        # Schema/migrations only; the mobile-review SYNC ENGINE and UI that read
        # and write these are deferred Stage-B leaves. Idempotent, so shipping the
        # empty tables in the base is harmless and lets those leaves land additively.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_mobile_battles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                revlog_id     INTEGER UNIQUE NOT NULL,
                card_id       INTEGER NOT NULL,
                ease          INTEGER NOT NULL,
                review_time   INTEGER NOT NULL,
                review_type   INTEGER NOT NULL,
                queued_at     INTEGER NOT NULL,
                resolved      INTEGER NOT NULL DEFAULT 0,
                resolved_at   INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mobile_battle_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         INTEGER NOT NULL,
                enemy_id          INTEGER NOT NULL,
                enemy_name        TEXT NOT NULL,
                enemy_level       INTEGER NOT NULL,
                enemy_shiny       INTEGER NOT NULL,
                companion_name    TEXT,
                companion_level   INTEGER,
                outcome           TEXT NOT NULL,
                xp_gained         INTEGER DEFAULT 0,
                trainer_xp_gained INTEGER DEFAULT 0,
                cash_gained       INTEGER DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON mobile_battle_history(timestamp)")

        # Durable record of revlog ids Ankimon already turned into battle progress
        # on desktop. Consulted by the mobile-review detection pass to exclude them
        # from the mobile queue even after a mid-session restart (which loses the
        # in-memory session set). Unlike advancing the watermark to these ids, this
        # keeps an OLDER not-yet-synced mobile review (lower revlog id) detectable:
        # rows here are pruned once the watermark advances past them.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS desktop_processed_reviews (
                revlog_id INTEGER PRIMARY KEY,
                card_id   INTEGER
            )
        """)

        conn.commit()
        self._log("info", "AnkimonDB: Database schema initialized.")
        try:
            cursor.execute(
                "SELECT value FROM metadata WHERE key = 'base_stats_normalized'"
            )
            marker = cursor.fetchone()
            if marker is None or marker[0] != "true":
                self._normalize_pokemon_base_stats()
                cursor.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) "
                    "VALUES ('base_stats_normalized', 'true')"
                )
                conn.commit()
        except Exception as e:
            self._log("error", f"Failed to run base_stats normalization on startup: {e}")

    def _normalize_pokemon_base_stats(self):
        """Sweeps all captured pokemon to ensure base_stats is populated (for older databases)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT individual_id, data FROM captured_pokemon")
        rows = cursor.fetchall()
        
        from ..functions.pokedex_functions import is_valid_base_stats, search_pokedex
        updates = []
        for row in rows:
            ind_id, obfuscated_data = row
            pokemon_data = self._deobfuscate(obfuscated_data)
            if not pokemon_data:
                continue
            
            base_stats = pokemon_data.get("base_stats")

            # If base_stats is completely missing or empty/lacking stat keys or invalid values
            if not is_valid_base_stats(base_stats):
                base_stats = search_pokedex(pokemon_data.get("name", ""), "baseStats") or {}
                if is_valid_base_stats(base_stats):
                    pokemon_data["base_stats"] = base_stats
                    new_obfuscated = self._obfuscate(pokemon_data)
                    updates.append((new_obfuscated, ind_id))

        if updates:
            self._log("info", f"Normalizing base_stats for {len(updates)} legacy pokemon records...")
            cursor.executemany("UPDATE captured_pokemon SET data = ? WHERE individual_id = ?", updates)
            conn.commit()
            self._clear_reviewer_ownership_cache()

    # --- Captured Pokemon Operations ---

    def save_pokemon(self, pokemon_data: Dict[str, Any]):
        """Saves or updates a captured pokemon. Preserves is_main flag if pokemon already exists."""
        individual_id = pokemon_data.get("individual_id")
        if not individual_id:
            self._log("error", "Cannot save pokemon without individual_id")
            return False

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if pokemon already exists to preserve is_main flag
        cursor.execute("SELECT is_main FROM captured_pokemon WHERE individual_id = ?", (individual_id,))
        row = cursor.fetchone()
        
        if row:
            # Update existing - preserve is_main
            cursor.execute(
                "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
                (obfuscated_data, individual_id)
            )
        else:
            # Insert new with is_main = 0
            cursor.execute(
                "INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)",
                (individual_id, obfuscated_data)
            )
        conn.commit()
        self._clear_reviewer_ownership_cache()

        # Automatically mark the pokemon as caught. The row is already committed,
        # so a failure here must not fail the save -- but it is logged as an error
        # (not a warning) because it means the Pokedex is now behind the
        # collection until _reconcile_pokedex_history heals it on the next launch.
        pokemon_id = pokemon_data.get("id")
        if pokemon_id:
            try:
                self.mark_as_caught(int(pokemon_id))
            except Exception as e:
                self._log("error", f"Failed to mark saved pokemon as caught: {e}")

        return True

    def get_pokemon(self, individual_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific pokemon by its individual_id."""
        cursor = self.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,)
        )
        row = cursor.fetchone()
        if row:
            return self._deobfuscate(row["data"])
        return None

    def get_all_pokemon(self) -> List[Dict[str, Any]]:
        """Retrieves all captured pokemon."""
        cursor = self.execute("SELECT data FROM captured_pokemon")
        results = []
        for row in cursor.fetchall():
            pokemon = self._deobfuscate(row["data"])
            if pokemon:
                results.append(pokemon)

        return results

    def has_pokemon_by_name(self, name: str) -> bool:
        """
        Efficiently checks if a pokemon with the given name exists in the collection.
        Uses a direct SQL query on the virtual name index.
        """
        cursor = self.execute("SELECT 1 FROM captured_pokemon WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,))
        return cursor.fetchone() is not None

    def _clear_reviewer_ownership_cache(self):
        """Clears the Reviewer_Manager's ownership cache and the internal Pokémon ID cache when database changes.

        Uses ``services.reviewer`` (the seam-correct reference on this branch) rather
        than ``mw.reviewer_obj`` (which is an exp-only pattern never set here).
        Calls the public ``invalidate_hud_cache()`` API instead of reaching into the
        private ``_ownership_cache`` dict directly.  Safe to call headless: the
        ``services`` import is always available; ``services.reviewer`` is ``None``
        outside of a live Anki session.
        """
        self._all_pokemon_ids_cache = None
        try:
            from ..services import services
            reviewer = services.reviewer
            if reviewer is not None and hasattr(reviewer, "invalidate_hud_cache"):
                reviewer.invalidate_hud_cache()
        except Exception:
            pass

    def delete_pokemon(self, individual_id: str) -> bool:
        """Deletes a pokemon from the captured collection."""
        cursor = self.execute(
            "DELETE FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,)
        )
        self._get_connection().commit()
        self._clear_reviewer_ownership_cache()
        return cursor.rowcount > 0

    def replace_pokemon(self, pokemon_data: Dict[str, Any], old_individual_id: str) -> bool:
        """Replaces a pokemon with the given individual_id with the given pokemon_data."""

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()

        new_individual_id = pokemon_data["individual_id"]

        # Are we trying to replace ourselves?
        if new_individual_id == old_individual_id:
            self._log("error", f"You already have this {pokemon_data['name']} in your collection!")
            return False


        # Does the pokemon being replaced exist?
        cursor.execute(
            "SELECT is_main FROM captured_pokemon WHERE individual_id = ?",
            (old_individual_id,)
        )
        row = cursor.fetchone()

        if row is None:
            self._log("error", f"No Pokémon found with individual_id {old_individual_id}")
            return False

        is_main = row[0]

        # Does the incoming Pokémon already exist somewhere else?
        cursor.execute(
            "SELECT 1 FROM captured_pokemon WHERE individual_id = ?",
            (new_individual_id,)
        )
        if cursor.fetchone() is not None:
            self._log("error", f"You already have this {pokemon_data['name']} in your collection!")
            return False

        # You passed all the checks. Full steam ahead!
        # Replace the row in-place
        cursor.execute(
            """
            UPDATE captured_pokemon
            SET individual_id = ?, is_main = ?, data = ?
            WHERE individual_id = ?
            """,
            (new_individual_id, is_main, obfuscated_data, old_individual_id)
        )

        conn.commit()
        self._clear_reviewer_ownership_cache()
        return cursor.rowcount > 0

    def get_pokemon_count(self) -> int:
        """Returns the count of captured pokemon."""
        cursor = self.execute("SELECT COUNT(*) FROM captured_pokemon")
        return cursor.fetchone()[0]

    def get_shiny_count(self) -> int:
        """Returns the count of shiny pokemon."""
        cursor = self.execute("SELECT COUNT(*) FROM captured_pokemon WHERE shiny = 1")
        return cursor.fetchone()[0]

    def execute(self, query: str, parameters: tuple = ()) -> "CursorWrapper":
        """Execute custom SQL and return a lease-holding cursor wrapper."""
        try:
            with self.lease_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, parameters)
                return cursor
        except sqlite3.DatabaseError as e:
            if ("malformed" in str(e).lower() or "disk image" in str(e).lower()) and not self._is_repairing:
                self.repair_database()
                # Retry once after repair
                with self.lease_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(query, parameters)
                    return cursor
            raise e

    def get_pokemons_by_individual_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Retrieves multiple pokemon by their individual_ids."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        cursor = self.execute(f"SELECT data FROM captured_pokemon WHERE individual_id IN ({placeholders})", ids)
        results = []
        for row in cursor.fetchall():
            pokemon = self._deobfuscate(row["data"])
            if pokemon:
                results.append(pokemon)
        return results

    def get_all_pokemon_ids(self) -> set:
        """Returns a set of all captured pokemon's pokedex IDs using the virtual index."""
        cursor = self.execute("SELECT pokedex_id FROM captured_pokemon WHERE pokedex_id IS NOT NULL")
        return {row[0] for row in cursor.fetchall()}

    # --- Main Pokemon Operations ---

    def save_main_pokemon(self, pokemon_data: Dict[str, Any]):
        """Saves/updates the main pokemon. Sets is_main=1 on this pokemon, is_main=0 on all others."""
        individual_id = pokemon_data.get("individual_id")
        if not individual_id:
            self._log("error", "Cannot save main pokemon without individual_id")
            return False

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Clear the main flag from all pokemon first
        cursor.execute("UPDATE captured_pokemon SET is_main = 0 WHERE is_main = 1")
        
        # Save/update this pokemon and set as main
        cursor.execute(
            "INSERT OR REPLACE INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 1, ?)",
            (individual_id, obfuscated_data)
        )
        conn.commit()
        self._clear_reviewer_ownership_cache()

        # Automatically mark the pokemon as caught (see save_pokemon: logged as an
        # error because the Pokedex is left behind the collection until the next
        # _reconcile_pokedex_history sweep).
        pokemon_id = pokemon_data.get("id")
        if pokemon_id:
            try:
                self.mark_as_caught(int(pokemon_id))
            except Exception as e:
                self._log("error", f"Failed to mark saved main pokemon as caught: {e}")

        return True

    def get_main_pokemon(self) -> Optional[Dict[str, Any]]:
        """Retrieves the main pokemon (the one with is_main=1)."""
        cursor = self.execute("SELECT data FROM captured_pokemon WHERE is_main = 1")
        row = cursor.fetchone()
        if row:
            return self._deobfuscate(row["data"])
        return None

    def set_main_pokemon(self, individual_id: str) -> bool:
        """Sets a pokemon as the main pokemon by individual_id. Returns False if pokemon not found."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if pokemon exists
        cursor.execute("SELECT individual_id FROM captured_pokemon WHERE individual_id = ?", (individual_id,))
        if not cursor.fetchone():
            return False
        
        # Clear old main
        cursor.execute("UPDATE captured_pokemon SET is_main = 0 WHERE is_main = 1")
        # Set new main
        cursor.execute("UPDATE captured_pokemon SET is_main = 1 WHERE individual_id = ?", (individual_id,))
        conn.commit()
        return True

    # --- Item Operations ---

    def add_item(self, item_name: str, quantity: int = 1, extra_data: Optional[Dict] = None, commit: bool = True) -> bool:
        """
        Adds a new item to the database with metadata discovery from items.csv.
        Use this for the first time an item is introduced (e.g. migration, looting).
        """
        item_id = None
        category_id = None
        cost = None
        fling_power = None
        fling_effect_id = None

        # Look up metadata from items.csv
        if Path(csv_file_items_cost).is_file():
            try:
                with open(csv_file_items_cost, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        if r['identifier'] == item_name:
                            item_id = int(r['id'])
                            if r.get('category_id'): category_id = int(r['category_id'])
                            if r.get('cost'): cost = int(r['cost'])
                            if r.get('fling_power'): fling_power = int(r['fling_power'])
                            if r.get('fling_effect_id'): fling_effect_id = int(r['fling_effect_id'])
                            break
            except Exception as e:
                self._log("error", f"Failed to look up item '{item_name}' in items.csv: {e}")

        return self.save_item(
            item_id, item_name, quantity, extra_data,
            category_id=category_id, cost=cost,
            fling_power=fling_power, fling_effect_id=fling_effect_id,
            commit=commit
        )

    def save_item(self, item_id: Optional[int], item_name: str, quantity: int, extra_data: Optional[Dict] = None,
                  category_id: Optional[int] = None, cost: Optional[int] = None, 
                  fling_power: Optional[int] = None, fling_effect_id: Optional[int] = None,
                  commit: bool = True) -> bool:
        """
        Low-level upsert for items. Lenient with metadata: if missing, tries to fetch from 
        existing DB records but DOES NOT perform CSV lookups.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Lenient metadata resolution: try to fetch existing metadata from DB if NOT provided
        if item_name and (item_id is None or cost is None or category_id is None):
            cursor.execute("SELECT id, category_id, cost, fling_power, fling_effect_id FROM items WHERE item_name = ?", (item_name,))
            row = cursor.fetchone()
            if row:
                if item_id is None: item_id = row["id"]
                if category_id is None: category_id = row["category_id"]
                if cost is None: cost = row["cost"]
                if fling_power is None: fling_power = row["fling_power"]
                if fling_effect_id is None: fling_effect_id = row["fling_effect_id"]

        # Ensure type: "TM" for UI filtering if applicable
        if category_id == 37:
            if extra_data is None: extra_data = {}
            if extra_data.get("type") != "TM": extra_data["type"] = "TM"

        obfuscated_data = self._obfuscate(extra_data) if extra_data else None
        cursor.execute(
            """INSERT OR REPLACE INTO items 
               (id, item_name, quantity, data, category_id, cost, fling_power, fling_effect_id) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, item_name, quantity, obfuscated_data, category_id, cost, fling_power, fling_effect_id)
        )
        if commit:
            conn.commit()
        return True

    def get_item(self, identifier: Any) -> Optional[Dict[str, Any]]:
        """Retrieves an item by name (identifier) or integer ID."""
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            field = "id"
        else:
            field = "item_name"
            
        cursor = self.execute(
            f"SELECT id, item_name, quantity, data, category_id, cost, fling_power, fling_effect_id FROM items WHERE {field} = ?",
            (identifier,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "item_name": row["item_name"],
                "quantity": row["quantity"],
                "extra_data": self._deobfuscate(row["data"]) if row["data"] else {},
                "category_id": row["category_id"],
                "cost": row["cost"],
                "fling_power": row["fling_power"],
                "fling_effect_id": row["fling_effect_id"]
            }
        return None

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Retrieves all items."""
        cursor = self.execute("SELECT id, item_name, quantity, data, category_id, cost, fling_power, fling_effect_id FROM items")
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "item_name": row["item_name"],
                "quantity": row["quantity"],
                "extra_data": self._deobfuscate(row["data"]) if row["data"] else {},
                "category_id": row["category_id"],
                "cost": row["cost"],
                "fling_power": row["fling_power"],
                "fling_effect_id": row["fling_effect_id"]
            })
        return results

    def update_item_quantity(self, item_name: str, delta: int) -> int:
        """Updates item quantity by delta. Returns new quantity."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get current quantity
        cursor.execute("SELECT quantity FROM items WHERE item_name = ?", (item_name,))
        row = cursor.fetchone()
        current_qty = row["quantity"] if row else 0
        if current_qty == 0:
            self._log("warning", f"Item '{item_name}' not found in inventory.")
            return 0
        new_qty = current_qty + delta
        if new_qty < 0:
            self._log("warning", f"Item '{item_name}' has insufficient quantity.")
            return current_qty

        if new_qty > 0:
            cursor.execute(
                "UPDATE items SET quantity = ? WHERE item_name = ?",
                (new_qty, item_name)
            )
        else:
            cursor.execute("DELETE FROM items WHERE item_name = ?", (item_name,))

        conn.commit()
        return new_qty

    # --- Badge Operations ---

    def save_badge(self, badge_id: str, badge_data: Dict[str, Any]):
        """Saves or updates a badge."""
        achieved = badge_data.get("achieved", "false")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO badges (badge_id, achieved) VALUES (?, ?)",
            (badge_id, achieved)
        )
        conn.commit()
        return True

    def get_badge(self, badge_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a badge by ID."""
        cursor = self.execute("SELECT * FROM badges WHERE badge_id = ?", (badge_id,))
        row = cursor.fetchone()
        if row:
            return {
                "badge_id": row["badge_id"],
                "achieved": row["achieved"]
            }
        return None

    def get_all_badges(self) -> List[Dict[str, Any]]:
        """Retrieves all badges."""
        cursor = self.execute("SELECT badge_id, achieved FROM badges")
        results = []
        for row in cursor.fetchall():
            badge = {
                "badge_id": row["badge_id"],
                "achieved": row["achieved"]
            }
            results.append(badge)
        return results

    # --- Team Operations ---

    def save_team(self, team_list: List[Dict[str, Any]]):
        """Saves the team composition. Replaces existing team."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # clear existing team
        cursor.execute("DELETE FROM team")
        
        for i, member in enumerate(team_list):
            individual_id = member.get("individual_id")
            if individual_id:
                cursor.execute(
                    "INSERT INTO team (slot_position, individual_id) VALUES (?, ?)",
                    (i + 1, individual_id)
                )
        conn.commit()
        return True

    def get_team(self) -> List[Dict[str, Any]]:
        """Retrieves the current team as a list of dicts with individual_id."""
        cursor = self.execute("SELECT individual_id FROM team ORDER BY slot_position ASC")
        results = []
        for row in cursor.fetchall():
            results.append({"individual_id": row["individual_id"]})
        return results

    # --- Pokemon History Operations ---

    def add_to_history(self, pokemon_data: Dict[str, Any]):
        """Adds a released pokemon to history."""
        # Ensure individual_id exists to avoid duplicates if possible, or just generate one
        individual_id = pokemon_data.get("individual_id") or str(uuid.uuid4())
        
        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO pokemon_history (individual_id, data) VALUES (?, ?)",
                (individual_id, obfuscated_data)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._log("warning", f"Pokemon {individual_id} already in history.")
            return False

    def get_history(self) -> List[Dict[str, Any]]:
        """Retrieves all released pokemon history."""
        cursor = self.execute("SELECT data FROM pokemon_history")
        results = []
        for row in cursor.fetchall():
            data = self._deobfuscate(row["data"])
            if data:
                results.append(data)
        return results

    # --- User Data Operations ---

    def set_user_data(self, key: str, value: Any):
        """Sets a user data key-value pair."""
        # Store as simple string if possible, or JSON string for complex objects
        str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_data (key, value) VALUES (?, ?)",
            (key, str_value)
        )
        conn.commit()
        return True

    def get_user_data(self, key: str, default: Any = None) -> Any:
        """Retrieves user data by key."""
        cursor = self.execute("SELECT value FROM user_data WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            val = row["value"]
            # Try to parse as JSON, fallback to string
            try:
                return json.loads(val)
            except:
                return val
        return default

    @staticmethod
    def _coerce_pokedex_id_list(raw: Any) -> List[int]:
        """Normalises a stored pokedex id list to plain ints.

        Legacy stores (and hand-edited/corrupt data) can hold string ids such as
        ``"25"`` alongside ints, or entries that are not ids at all. Without
        normalising, ``25 not in ["25"]`` is True (so the id is appended a
        second time) and the resulting mixed set breaks both the Ankidex's
        ``seen - caught`` subtraction and profile_data's ``search_pokedex_by_id``
        lookup. Unhashable/garbage entries are dropped rather than allowed to
        raise out of the getters.
        """
        if not isinstance(raw, list):
            return []
        ids: List[int] = []
        for entry in raw:
            try:
                ids.append(int(entry))
            except (TypeError, ValueError):
                continue
        return ids

    def _append_pokedex_ids(self, additions: Dict[str, Iterable[Any]]) -> Dict[str, int]:
        """Append ids to the pokedex ``user_data`` lists in ONE transaction.

        ``additions`` maps ``"pokedex_caught"`` / ``"pokedex_seen"`` to the ids
        to record. Every key is read, merged and rewritten on the same
        connection and committed exactly once, so the two lists cannot diverge:
        either both land or neither does. Driving them through two
        ``set_user_data`` calls instead (a commit each) can persist a caught id
        whose matching seen id was lost to whatever failed in between — a disk
        error, the busy-timeout expiring under write contention, or the process
        going away — leaving the Ankidex with a species it counts as caught but
        never saw, and no way to tell that happened.

        The commit goes through the ConnectionWrapper rather than the raw
        sqlite3 handle so a bulk mobile "Resolve All" (which sets
        ``_disable_commit`` and holds one long write transaction) still folds
        these writes into its outer transaction and rolls them back with it.

        Callers must hold ``self._pokedex_lock``. Returns the number of ids
        newly added per key.
        """
        conn = self._get_connection()
        pending: List[tuple] = []
        added: Dict[str, int] = {}

        for key, ids in additions.items():
            # De-duplicated on the way out as well as in: a list that a legacy
            # write left holding "25" alongside 25 is stored back normalised
            # once we have a reason to rewrite it.
            known: set = set()
            stored: List[int] = []
            for pokemon_id in self._coerce_pokedex_id_list(self.get_user_data(key, [])):
                if pokemon_id not in known:
                    known.add(pokemon_id)
                    stored.append(pokemon_id)

            new_ids: List[int] = []
            for raw_id in ids:
                try:
                    pokemon_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if pokemon_id not in known:
                    known.add(pokemon_id)
                    new_ids.append(pokemon_id)

            added[key] = len(new_ids)
            if new_ids:
                pending.append((key, json.dumps(stored + new_ids)))

        if pending:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT OR REPLACE INTO user_data (key, value) VALUES (?, ?)",
                    pending,
                )
            conn.commit()
        return added

    def mark_as_caught(self, pokemon_id: int):
        """Marks a pokemon as caught (and seen) in the pokedex history.

        Raises if the write fails — callers on a save path swallow and log that,
        but the failure must be visible rather than reported as a success.
        """
        try:
            pokemon_id = int(pokemon_id)
        except (TypeError, ValueError):
            self._log("warning", f"Ignoring non-numeric pokedex id: {pokemon_id!r}")
            return

        # Both lists are rewritten wholesale, so hold the lock across the whole
        # read-modify-write (save_pokemon also runs on the mobile-sync thread).
        # Catching implies seeing, so both are recorded in the same transaction.
        with self._pokedex_lock:
            self._append_pokedex_ids(
                {"pokedex_caught": (pokemon_id,), "pokedex_seen": (pokemon_id,)}
            )

    def _reconcile_pokedex_history_safely(self):
        """``_reconcile_pokedex_history`` that can never stop the DB from opening."""
        try:
            self._reconcile_pokedex_history()
        except Exception as e:
            self._log("error", f"Failed to reconcile pokedex history: {e}")

    def _reconcile_pokedex_history(self):
        """Heal the caught/seen lists from the Pokemon the DB still knows about.

        ``mark_as_caught`` is best-effort at every call site — ``save_pokemon``
        and the evolution window log a failure and carry on rather than fail the
        catch — so one locked or failed write would otherwise drop a species
        from the Ankidex permanently once its ``captured_pokemon`` row is
        overwritten by an evolution. This idempotent startup sweep re-derives
        the lists from the rows that are still authoritative (every owned
        Pokemon, plus every released one in ``pokemon_history``), so a missed
        mark heals on the next launch instead of becoming silent data loss.

        It doubles as the backfill for databases written before the caught list
        existed: those start empty, and without this the list only ever covers
        Pokemon saved after the upgrade.
        """
        ids: set = set()

        try:
            cursor = self.execute(
                "SELECT DISTINCT pokedex_id FROM captured_pokemon "
                "WHERE pokedex_id IS NOT NULL"
            )
            ids.update(
                self._coerce_pokedex_id_list([row[0] for row in cursor.fetchall()])
            )
        except Exception as e:
            self._log("warning", f"Pokedex reconcile: could not read captured_pokemon: {e}")

        # Released Pokemon: still caught for Pokedex purposes. Wrapped separately
        # so an older DB without pokemon_history still reconciles the owned rows.
        try:
            cursor = self.execute(
                "SELECT DISTINCT json_extract(data, '$.id') FROM pokemon_history"
            )
            ids.update(
                self._coerce_pokedex_id_list([row[0] for row in cursor.fetchall()])
            )
        except Exception as e:
            self._log("warning", f"Pokedex reconcile: could not read pokemon_history: {e}")

        if not ids:
            return

        sorted_ids = sorted(ids)
        with self._pokedex_lock:
            added = self._append_pokedex_ids(
                {"pokedex_caught": sorted_ids, "pokedex_seen": sorted_ids}
            )
        if any(added.values()):
            self._log(
                "info",
                "Pokedex history reconciled from stored Pokemon: "
                f"+{added.get('pokedex_caught', 0)} caught, "
                f"+{added.get('pokedex_seen', 0)} seen.",
            )

    def get_caught_ids(self) -> set[int]:
        """Returns a set of all pokemon IDs explicitly marked as caught."""
        return set(self._coerce_pokedex_id_list(self.get_user_data("pokedex_caught", [])))

    def get_seen_ids(self) -> set[int]:
        """Returns a set of all pokemon IDs marked as seen."""
        return set(self._coerce_pokedex_id_list(self.get_user_data("pokedex_seen", [])))

    def get_all_user_data(self) -> Dict[str, Any]:
        """Retrieves all user data as a dictionary."""
        cursor = self.execute("SELECT key, value FROM user_data")
        result = {}
        for row in cursor.fetchall():
            key = row["key"]
            val = row["value"]
            try:
                result[key] = json.loads(val)
            except:
                result[key] = val
        return result

    def migrate_user_data_to_config(self, key_map: Dict[str, str]) -> Dict[str, str]:
        """Move legacy ``user_data`` values into empty config keys atomically.

        Existing config values always win. Legacy rows are retired only in the
        same transaction that persists any missing replacements, so a failed
        config write cannot destroy the user's original value.
        """
        migrated: Dict[str, str] = {}
        conn = self._get_connection()
        with conn:
            with conn.cursor() as cursor:
                for legacy_key, config_key in key_map.items():
                    cursor.execute(
                        "SELECT value FROM user_data WHERE key = ?", (legacy_key,)
                    )
                    legacy_row = cursor.fetchone()
                    legacy_value = (
                        ""
                        if legacy_row is None or legacy_row["value"] is None
                        else str(legacy_row["value"])
                    )

                    cursor.execute(
                        "SELECT value FROM config WHERE key = ?", (config_key,)
                    )
                    config_row = cursor.fetchone()
                    config_value = (
                        ""
                        if config_row is None or config_row["value"] is None
                        else str(config_row["value"])
                    )

                    if not config_value and legacy_value:
                        cursor.execute(
                            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                            (config_key, legacy_value),
                        )
                        config_value = legacy_value
                        migrated[config_key] = legacy_value

                    if legacy_value and config_value:
                        cursor.execute(
                            "DELETE FROM user_data WHERE key = ?", (legacy_key,)
                        )

        return migrated

    # --- Config Operations (replaces config.obf) ---

    def set_config_value(self, key: str, value: Any):
        """Sets a config key-value pair."""
        # Store as JSON string to preserve type information
        str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, str_value)
        )
        conn.commit()
        return True

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Retrieves a config value by key."""
        cursor = self.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            val = row["value"]
            # Try to parse as JSON, fallback to string
            try:
                return json.loads(val)
            except:
                return val
        return default

    def get_all_config(self) -> Dict[str, Any]:
        """Retrieves all config settings as a dictionary."""
        cursor = self.execute("SELECT key, value FROM config")
        result = {}
        for row in cursor.fetchall():
            key = row["key"]
            val = row["value"]
            try:
                result[key] = json.loads(val)
            except:
                result[key] = val
        return result

    def save_all_config(self, config_dict: Dict[str, Any]):
        """Bulk saves a config dictionary to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in config_dict.items():
            str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            cursor.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, str_value)
            )
        conn.commit()
        return True

    def set_config_value(self, key: str, value: Any) -> bool:
        """Upsert a SINGLE config key (incremental). Avoids rewriting all ~60 config
        rows on every Settings.set — the battle loop awards cash per review, so the
        old save_all_config path rewrote the whole table dozens of times per battle."""
        str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        conn = self._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, str_value),
        )
        conn.commit()
        return True

    def has_config(self) -> bool:
        """Checks if config data exists in the database."""
        cursor = self.execute("SELECT COUNT(*) FROM config")
        return cursor.fetchone()[0] > 0

    def get_stats(self) -> Dict[str, int]:
        """Returns a summary of database contents for synchronization/backup comparison."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        # Count pokemon
        cursor.execute("SELECT COUNT(*) as count FROM captured_pokemon")
        stats["pokemon"] = cursor.fetchone()["count"]
        
        # Count items
        cursor.execute("SELECT COUNT(*) as count FROM items")
        stats["items"] = cursor.fetchone()["count"]
        
        # Count history
        cursor.execute("SELECT COUNT(*) as count FROM pokemon_history")
        stats["history"] = cursor.fetchone()["count"]
        
        # Count badges
        cursor.execute("SELECT COUNT(*) as count FROM badges")
        stats["badges"] = cursor.fetchone()["count"]
        
        return stats

    # --- Migration from JSON Files ---

    def migrate_from_json(self, mypokemon_path: Path, mainpokemon_path: Path,
                          items_path: Path, badges_path: Path,
                          team_path: Path = None, history_path: Path = None,
                          data_path: Path = None, rate_path: Path = None) -> Dict[str, int]:
        """
        Migrates data from JSON files to the database.
        Returns a dict with counts of migrated items.
        """
        stats = {"pokemon": 0, "main": 0, "items": 0, "badges": 0, 
                 "team": 0, "history": 0, "userdata": 0}

        # Check if already migrated
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated_phase2'")
        if cursor.fetchone():
            self._log("info", "Database Phase 2 (full) already migrated. Checking Phase 1...")
            # If Phase 2 is done, Phase 1 is definitely done.
            return stats
        
        # Check Phase 1 migration (captured, items, badges)
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated'")
        phase1_done = cursor.fetchone() is not None

        if not phase1_done:
            # Migrate mypokemon.json
            if mypokemon_path.is_file():
                try:
                    with open(mypokemon_path, 'r', encoding='utf-8') as f:
                        pokemon_list = json.load(f)
                    for pokemon in pokemon_list:
                        if self.save_pokemon(pokemon):
                            stats["pokemon"] += 1
                    self._log("info", f"Migrated {stats['pokemon']} pokemon from mypokemon.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate mypokemon.json: {e}")

            # Migrate mainpokemon.json
            if mainpokemon_path.is_file():
                try:
                    with open(mainpokemon_path, 'r', encoding='utf-8') as f:
                        main_data = json.load(f)
                    if main_data:
                        # mainpokemon.json is a list with one item
                        main_pokemon = main_data[0] if isinstance(main_data, list) else main_data
                        if self.save_main_pokemon(main_pokemon):
                            stats["main"] = 1
                    self._log("info", "Migrated main pokemon from mainpokemon.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate mainpokemon.json: {e}")

            # Migrate items.json
            if items_path.is_file():
                try:
                    with open(items_path, 'r', encoding='utf-8') as f:
                        items_list = json.load(f)
                    
                    for item in items_list:
                        if not item: continue
                        # Support multiple legacy keys for item name
                        item_name = item.get("item") or item.get("name") or item.get("item_name")
                        quantity = item.get("quantity", item.get("amount", 1))
                        if item_name:
                            if self.add_item(item_name, quantity, extra_data=item, commit=False):
                                stats["items"] += 1
                    
                    self._get_connection().commit()
                    self._log("info", f"Migrated {stats['items']} items from items.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate items.json: {e}")

            # Migrate badges.json - handles both [1, 2, 3] and [{"id": 1}, ...] formats
            if badges_path.is_file():
                try:
                    with open(badges_path, 'r', encoding='utf-8') as f:
                        badges_list = json.load(f)
                    for badge in badges_list:
                        # Handle both integer, string, and dict formats
                        if isinstance(badge, (int, str)):
                            badge_id = str(badge)
                            badge_data = {"achieved": True}
                        else:
                            badge_id = str(badge.get("id", badge.get("badge_id", "")))
                            # Ensure we have achieved status preserved
                            badge_data = badge
                            badge_data["achieved"] = True
                                
                        if badge_id:
                            self.save_badge(badge_id, badge_data)
                            stats["badges"] += 1
                    self._log("info", f"Migrated {stats['badges']} badges from badges.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate badges.json: {e}")
            
            # Mark Phase 1 as done
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', 'true')")

        # --- Phase 2 Migration (Team, History, UserData) ---
        
        # Migrate team.json
        if team_path and team_path.is_file():
            try:
                with open(team_path, 'r', encoding='utf-8') as f:
                    team_list = json.load(f)
                if self.save_team(team_list):
                    stats["team"] = len(team_list)
                self._log("info", f"Migrated {stats['team']} team members from team.json")
            except Exception as e:
                self._log("error", f"Failed to migrate team.json: {e}")

        # Migrate pokemon_history.json
        if history_path and history_path.is_file():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history_list = json.load(f)
                for pokemon in history_list:
                    if self.add_to_history(pokemon):
                        stats["history"] += 1
                self._log("info", f"Migrated {stats['history']} history entries from pokemon_history.json")
            except Exception as e:
                self._log("error", f"Failed to migrate pokemon_history.json: {e}")

        # Migrate data.json (User Credentials)
        if data_path and data_path.is_file():
            try:
                with open(data_path, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                count = 0
                for key, value in user_data.items():
                    self.set_user_data(key, value)
                    count += 1
                stats["userdata"] = count
                self._log("info", f"Migrated {stats['userdata']} keys from data.json")
            except Exception as e:
                self._log("error", f"Failed to migrate data.json: {e}")

        # Step 8: Migrate rate_this.json
        if rate_path and rate_path.is_file():
            try:
                with open(rate_path, 'r', encoding='utf-8') as f:
                    rate_data = json.load(f)
                
                if isinstance(rate_data, dict) and rate_data.get("rate_this") in (True, "true"):
                    self.set_user_data("rate_this", True)
                    self._log("info", "Migrated rate_this.json")
            except Exception as e:
                self._log("error", f"Failed to migrate rate_this.json: {e}")

        # Mark Phase 2 as done
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')")
        conn.commit()

        # --- Integrity Check ---
        # Verify that database counts match expected counts from JSON files
        integrity_issues = []
        
        # Count JSON entries
        json_counts = {"pokemon": 0, "items": 0, "badges": 0}
        try:
            if mypokemon_path.is_file():
                with open(mypokemon_path, 'r', encoding='utf-8') as f:
                    json_counts["pokemon"] = len(json.load(f))
            if items_path.is_file():
                with open(items_path, 'r', encoding='utf-8') as f:
                    json_counts["items"] = len(json.load(f))
            if badges_path.is_file():
                with open(badges_path, 'r', encoding='utf-8') as f:
                    json_counts["badges"] = len(json.load(f))
        except Exception as e:
            self._log("warning", f"Could not read JSON files for integrity check: {e}")
        
        # Count database entries
        db_counts = {"pokemon": 0, "items": 0, "badges": 0}
        cursor.execute("SELECT COUNT(*) FROM captured_pokemon")
        db_counts["pokemon"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM items")
        db_counts["items"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM badges")
        db_counts["badges"] = cursor.fetchone()[0]
        
        # Compare counts
        for key in ["pokemon", "items", "badges"]:
            if json_counts[key] > 0 and db_counts[key] < json_counts[key]:
                integrity_issues.append(
                    f"{key}: JSON has {json_counts[key]} entries but DB only has {db_counts[key]}"
                )
        
        if integrity_issues:
            self._log("warning", f"Migration integrity issues detected: {integrity_issues}")
            stats["integrity_issues"] = integrity_issues
        else:
            self._log("info", "Migration integrity check passed - all counts match.")

        self._log("info", f"Migration complete: {stats}")
        return stats

    # --- Utility ---

    def is_migrated(self) -> bool:
        """Checks if ALL JSON data (Phase 1 & 2) has been migrated to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated_phase2'")
        row = cursor.fetchone()
        return row is not None and row["value"] == "true"

    def is_migrated_phase1(self) -> bool:
        """Checks if Phase 1 data (pokemon, items, badges) has been migrated."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated'")
        row = cursor.fetchone()
        return row is not None and row["value"] == "true"

    # --- Mobile Sync Operations ---
    # Deferred F25 leaf accessors for the mobile-review sync engine (F14/F29).
    # The pending_mobile_battles + mobile_battle_history tables already ship in
    # the base schema (empty/idempotent); these methods are the leaf's read/write
    # surface and were held back until the mobile engine landed.

    def get_mobile_watermark(self) -> int:
        """Return stored watermark (ms). Returns 0 if not set (first-ever run)."""
        row = self.execute(
            "SELECT value FROM metadata WHERE key = 'mobile_revlog_watermark'"
        ).fetchone()
        if row:
            return int(row[0])
        if self.is_migrated():
            # ``force=True`` is load-bearing, not an optimisation: the
            # force=False path of ``set_mobile_watermark`` calls back into
            # this getter to clamp monotonically, which would recurse forever.
            import time
            now_ms = int(time.time() * 1000)
            self.set_mobile_watermark(now_ms, force=True)
            return now_ms
        return 0

    def set_mobile_watermark(self, watermark_ms: int, *, force: bool = False) -> None:
        # Monotonic by default: the watermark must never move backwards. A
        # regression would re-expose already-processed reviews as "new" mobile
        # battles on the next sync (double XP / phantom battles), so clamp to the
        # current value. ``force=True`` is the escape hatch for an intentional
        # reset (e.g. a future "reprocess mobile reviews" tool).
        watermark_ms = int(watermark_ms)
        if not force:
            watermark_ms = max(watermark_ms, self.get_mobile_watermark())
        with self._get_connection():
            self._get_connection().execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_revlog_watermark', ?)",
                (str(watermark_ms),)
            )
            # Any desktop-processed id at or below the watermark is already
            # excluded by the `id > watermark` detection filter, so the explicit
            # record is redundant — prune it to keep the table bounded.
            try:
                self._get_connection().execute(
                    "DELETE FROM desktop_processed_reviews WHERE revlog_id <= ?",
                    (int(watermark_ms),)
                )
            except Exception:
                pass

    def record_desktop_processed_review(self, revlog_id: int, card_id: Optional[int] = None) -> None:
        """Durably record a revlog id Ankimon handled on desktop, so a mid-session
        restart can't re-expose it as a mobile review on the next sync."""
        if not revlog_id:
            return
        with self._get_connection():
            self._get_connection().execute(
                "INSERT OR IGNORE INTO desktop_processed_reviews (revlog_id, card_id) VALUES (?, ?)",
                (int(revlog_id), card_id)
            )

    def get_desktop_processed_revlog_ids(self) -> set:
        """Return the durably-recorded desktop-processed revlog ids."""
        try:
            rows = self.execute("SELECT revlog_id FROM desktop_processed_reviews").fetchall()
            return {int(r[0]) for r in rows}
        except Exception:
            return set()

    def clear_desktop_processed_reviews(self) -> None:
        with self._get_connection():
            self._get_connection().execute("DELETE FROM desktop_processed_reviews")

    def queue_mobile_battles(self, reviews: list[dict]) -> int:
        """Insert mobile reviews into pending queue. Returns count inserted (skips duplicates)."""
        import time
        now = int(time.time() * 1000)
        inserted = 0
        conn = self._get_connection()
        with conn:
            for r in reviews:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO pending_mobile_battles
                       (revlog_id, card_id, ease, review_time, review_type, queued_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (r["id"], r["cid"], r["ease"], r["time"], r["type"], now)
                )
                inserted += cursor.rowcount
        return inserted

    def get_pending_mobile_count(self) -> int:
        return self.execute(
            "SELECT COUNT(*) FROM pending_mobile_battles WHERE resolved = 0"
        ).fetchone()[0]

    def get_next_pending_mobile_batch(self, limit: int = 1) -> list[dict]:
        """Return next N unresolved battles, oldest-first (lowest revlog_id first)."""
        rows = self.execute(
            """SELECT id, revlog_id, card_id, ease, review_time, review_type
               FROM pending_mobile_battles
               WHERE resolved = 0
               ORDER BY revlog_id ASC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        keys = ["queue_id", "revlog_id", "card_id", "ease", "review_time", "review_type"]
        return [dict(zip(keys, r)) for r in rows]

    def mark_mobile_battle_resolved(self, queue_id: int) -> None:
        import time
        now = int(time.time() * 1000)
        cursor = self.execute("SELECT revlog_id FROM pending_mobile_battles WHERE id = ?", (queue_id,))
        row = cursor.fetchone()
        revlog_id = row[0] if row else None

        with self._get_connection():
            self._get_connection().execute(
                "UPDATE pending_mobile_battles SET resolved=1, resolved_at=? WHERE id=?",
                (now, queue_id)
            )

        if revlog_id:
            if self._deferred_mirror_revlog_ids is not None:
                # Inside a bulk "Resolve All" transaction: defer the mirror sync so
                # it doesn't commit resolved=1 on the other DB before (or despite) a
                # rollback of the primary transaction.
                self._deferred_mirror_revlog_ids.append(revlog_id)
            else:
                self.sync_resolutions_to_other_db([revlog_id], now)

    def begin_deferred_mirror_sync(self) -> None:
        """Start collecting mirror-DB resolutions instead of syncing them
        immediately. Call before a bulk 'Resolve All' transaction."""
        self._deferred_mirror_revlog_ids = []

    def flush_deferred_mirror_sync(self) -> None:
        """Sync all deferred resolutions to the mirror DB and stop deferring.
        Call only AFTER the bulk transaction has committed successfully."""
        import time
        ids = self._deferred_mirror_revlog_ids
        self._deferred_mirror_revlog_ids = None
        if ids:
            self.sync_resolutions_to_other_db(ids, int(time.time() * 1000))

    def discard_deferred_mirror_sync(self) -> None:
        """Drop any deferred resolutions without syncing (e.g. the bulk
        transaction rolled back) and stop deferring."""
        self._deferred_mirror_revlog_ids = None

    def add_mobile_history_entry(self, entry: Dict[str, Any]) -> bool:
        """Saves a single mobile battle outcome to history."""
        return self.add_mobile_history_entries_batch([entry])

    def add_mobile_history_entries_batch(self, entries: List[Dict[str, Any]]) -> bool:
        """Saves a batch of mobile battle outcomes to history in a single transaction."""
        if not entries:
            return True

        def _clean_val(v, default):
            if v is None:
                return default
            return v

        try:
            conn = self._get_connection()
            with conn:
                conn.executemany(
                    """INSERT INTO mobile_battle_history (
                        timestamp, enemy_id, enemy_name, enemy_level, enemy_shiny,
                        companion_name, companion_level, outcome, xp_gained,
                        trainer_xp_gained, cash_gained
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            _clean_val(entry.get("timestamp"), 0),
                            _clean_val(entry.get("enemy_id"), 0),
                            str(_clean_val(entry.get("enemy_name"), "")),
                            _clean_val(entry.get("enemy_level"), 0),
                            1 if entry.get("enemy_shiny") else 0,
                            str(_clean_val(entry.get("companion_name"), "")),
                            _clean_val(entry.get("companion_level"), 0),
                            str(_clean_val(entry.get("outcome"), "")),
                            _clean_val(entry.get("xp_gained"), 0),
                            _clean_val(entry.get("trainer_xp_gained"), 0),
                            _clean_val(entry.get("cash_gained"), 0),
                        )
                        for entry in entries
                    ]
                )
                conn.execute(
                    """DELETE FROM mobile_battle_history
                       WHERE id NOT IN (
                           SELECT id FROM mobile_battle_history
                           ORDER BY timestamp DESC, id DESC
                           LIMIT 500
                       )"""
                )
            return True
        except Exception as e:
            self._log("error", f"Failed to batch add mobile history entries: {e}")
            return False

    def get_mobile_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Retrieves recent mobile battle history entries, newest first."""
        try:
            rows = self.execute(
                """SELECT id, timestamp, enemy_id, enemy_name, enemy_level, enemy_shiny,
                          companion_name, companion_level, outcome, xp_gained,
                          trainer_xp_gained, cash_gained
                   FROM mobile_battle_history
                   ORDER BY timestamp DESC, id DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            keys = [
                "id", "timestamp", "enemy_id", "enemy_name", "enemy_level", "enemy_shiny",
                "companion_name", "companion_level", "outcome", "xp_gained",
                "trainer_xp_gained", "cash_gained"
            ]
            result = []
            for r in rows:
                item = dict(zip(keys, r))
                item["enemy_shiny"] = bool(item["enemy_shiny"])
                result.append(item)
            return result
        except Exception as e:
            self._log("error", f"Failed to get mobile history: {e}")
            return []

    def clear_mobile_history(self) -> bool:
        """Clears all entries from the mobile battle history."""
        try:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM mobile_battle_history")
            return True
        except Exception as e:
            self._log("error", f"Failed to clear mobile history: {e}")
            return False

    def sync_resolutions_to_other_db(self, revlog_ids: list[int], resolved_at: int) -> None:
        """
        If the other database exists (normal vs dev), sync the resolved status of the given
        revlog_ids to it directly.
        """
        if not revlog_ids:
            return

        current_name = self.db_path.name
        if current_name == "ankimon.db":
            other_name = "ankimonDEV.db"
        elif current_name == "ankimonDEV.db":
            other_name = "ankimon.db"
        else:
            return

        other_path = user_path / other_name
        if not other_path.is_file():
            return

        try:
            import sqlite3
            conn = sqlite3.connect(str(other_path), timeout=5.0)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_mobile_battles (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        revlog_id     INTEGER UNIQUE NOT NULL,
                        card_id       INTEGER NOT NULL,
                        ease          INTEGER NOT NULL,
                        review_time   INTEGER NOT NULL,
                        review_type   INTEGER NOT NULL,
                        queued_at     INTEGER NOT NULL,
                        resolved      INTEGER NOT NULL DEFAULT 0,
                        resolved_at   INTEGER
                    )
                """)
                placeholders = ",".join("?" for _ in revlog_ids)
                conn.execute(
                    f"UPDATE pending_mobile_battles SET resolved=1, resolved_at=? WHERE revlog_id IN ({placeholders})",
                    [resolved_at] + list(revlog_ids)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self._log("error", f"Failed to sync resolutions to {other_name}: {e}")


# Singleton instance for use throughout the addon
_db_instance: Optional[AnkimonDB] = None


def get_db(logger=None, db_path=None) -> AnkimonDB:
    """Gets the singleton database instance.

    ``db_path`` lets a caller build the singleton against a specific database
    file (multi-profile / hot-reload account preservation); when omitted the
    default ``ankimon.db`` under ``user_path`` is used. It is only honoured when
    the singleton does not yet exist — call :func:`reset_db` first to rebuild
    against a different path.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = AnkimonDB(logger, db_path=db_path)
    return _db_instance


def reset_db() -> None:
    """Drop the cached singleton (closing its connection).

    For test / agent-harness isolation: lets a fresh ``user_path`` produce a
    fresh database within the same process. Never called in normal Anki use.
    """
    global _db_instance
    if _db_instance is not None:
        try:
            _db_instance.close()
        except Exception:
            pass
    _db_instance = None
