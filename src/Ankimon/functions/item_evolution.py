"""Atomic persistence for an item evolution prepared by the UI."""

import contextlib
import json
import sqlite3
import time


# Match the DB’s 30-second budget without holding _pokedex_lock throughout.
_LOCK_TOTAL_BUDGET_S = 30.0
_LOCK_ATTEMPT_TIMEOUT_MS = 250
_LOCK_RETRY_BACKOFF_S = 0.05


def _is_lock_contention(error) -> bool:
    """Return whether SQLite reported lock contention."""
    message = str(error).lower()
    return "locked" in message or "busy" in message


@contextlib.contextmanager
def _attempt_lock_timeout(db, conn):
    """Limit each wait while holding _pokedex_lock.

    Mobile sync takes the SQLite write lock before _pokedex_lock. Short waits
    let the retry loop release our lock so the other writer can finish."""
    conn.execute(f"PRAGMA busy_timeout={_LOCK_ATTEMPT_TIMEOUT_MS};")
    try:
        yield
    finally:
        try:
            conn.execute(
                f"PRAGMA busy_timeout={getattr(db, '_BUSY_TIMEOUT_MS', 30000)};"
            )
        except Exception:
            # Preserve the original database error.
            pass


@contextlib.contextmanager
def _immediate_transaction(conn):
    """Acquire write intent before reading so SQLite can wait for other writers.

    Use the leased raw connection: wrapper auto-repair could replay a statement
    on another connection and split the transaction."""
    conn.acquire_lease()
    try:
        conn._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            try:
                conn._conn.rollback()
            except Exception:
                pass
            raise
        # Roll back and propagate commit failures.
        try:
            conn._conn.commit()
        except Exception:
            try:
                conn._conn.rollback()
            except Exception:
                pass
            raise
    finally:
        conn.release_lease()


def save_item_evolution(db, expected_pokemon, evolved_pokemon, item_name) -> bool:
    """Atomically save the evolved Pokémon, charge one item, and record history.

    Return False for a stale/deleted expected_pokemon or missing item. Database
    failures raise after rollback; lock contention retries for up to 30 seconds.
    Requires its own transaction and the complete pre-dialog Pokémon record."""
    individual_id = expected_pokemon.get("individual_id")
    if (
        not individual_id
        or individual_id != evolved_pokemon.get("individual_id")
        or not item_name
    ):
        return False
    species_ids = (int(expected_pokemon["id"]), int(evolved_pokemon["id"]))
    evolved_data = db._obfuscate(evolved_pokemon)

    deadline = time.monotonic() + _LOCK_TOTAL_BUDGET_S
    while True:
        try:
            committed = _commit_once(
                db,
                individual_id,
                expected_pokemon,
                evolved_data,
                item_name,
                species_ids,
            )
        except sqlite3.OperationalError as error:
            # Failed attempts roll back before retrying.
            if not _is_lock_contention(error) or time.monotonic() >= deadline:
                raise
            time.sleep(_LOCK_RETRY_BACKOFF_S)
            continue

        if committed:
            db._clear_reviewer_ownership_cache()
        return committed


def _commit_once(
    db, individual_id, expected_pokemon, evolved_data, item_name, species_ids
) -> bool:
    """Attempt one transaction; propagate contention to the retry loop."""
    # No callbacks or Qt work while holding the history lock and transaction.
    with db._pokedex_lock, db.lease_connection() as conn:
        if (
            conn.in_transaction
            or conn._disable_commit
            or getattr(conn, "_txn_depth", 0)
        ):
            raise RuntimeError("Item evolution requires its own transaction")

        with _attempt_lock_timeout(db, conn):
            return _write_evolution(
                db,
                conn,
                individual_id,
                expected_pokemon,
                evolved_data,
                item_name,
                species_ids,
            )


def _write_evolution(
    db, conn, individual_id, expected_pokemon, evolved_data, item_name, species_ids
) -> bool:
    """Write the evolution, item charge, and history in one transaction."""
    # Acquire the cursor before BEGIN; raw execution cannot replay on a new connection.
    with conn.cursor() as cursor, _immediate_transaction(conn):
        cursor.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,),
        )
        row = cursor.fetchone()
        if row is None or db._deobfuscate(row["data"]) != expected_pokemon:
            return False

        cursor.execute(
            "UPDATE items SET quantity = quantity - 1 "
            "WHERE item_name = ? AND quantity >= 1",
            (item_name,),
        )
        if cursor.rowcount != 1:
            return False

        cursor.execute(
            "UPDATE captured_pokemon SET data = ? WHERE individual_id = ? AND data = ?",
            (evolved_data, individual_id, row["data"]),
        )
        if cursor.rowcount != 1:
            # Returning False would commit the charge; raise to roll it back.
            raise RuntimeError("The Pokémon changed during item evolution")
        cursor.execute(
            "DELETE FROM items WHERE item_name = ? AND quantity = 0",
            (item_name,),
        )

        # DB history helpers commit independently, so use this transaction’s cursor.
        for key in ("pokedex_caught", "pokedex_seen"):
            cursor.execute("SELECT value FROM user_data WHERE key = ?", (key,))
            history = cursor.fetchone()
            try:
                existing = json.loads(history["value"]) if history else []
            except (TypeError, ValueError):
                # Recover malformed history; startup reconciliation restores other caught IDs.
                existing = []
            ids = list(dict.fromkeys(db._coerce_pokedex_id_list(existing)))
            for species_id in species_ids:
                if species_id not in ids:
                    ids.append(species_id)
            cursor.execute(
                "INSERT OR REPLACE INTO user_data (key, value) VALUES (?, ?)",
                (key, json.dumps(ids)),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Could not save item evolution history")

    return True
