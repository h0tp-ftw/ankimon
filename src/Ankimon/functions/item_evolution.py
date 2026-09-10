"""Atomic persistence for an item evolution prepared by the UI."""

import contextlib
import json
import sqlite3
import time


# One attempt may hold ``_pokedex_lock`` only briefly, so the sqlite wait per
# attempt is short and the retry loop below supplies the patience instead. The
# total budget matches AnkimonDB's own ``busy_timeout``, so an evolution waits
# out a bulk mobile resolve for as long as an ordinary save would.
_LOCK_TOTAL_BUDGET_S = 30.0
_LOCK_ATTEMPT_TIMEOUT_MS = 250
_LOCK_RETRY_BACKOFF_S = 0.05


def _is_lock_contention(error) -> bool:
    """Whether ``error`` is sqlite refusing to wait any longer for a lock."""
    message = str(error).lower()
    return "locked" in message or "busy" in message


@contextlib.contextmanager
def _attempt_lock_timeout(db, conn):
    """Shorten this connection's busy wait for the duration of one attempt.

    This module holds ``_pokedex_lock`` across its transaction, while the bulk
    mobile resolve takes the same two in the opposite order (write lock first,
    then ``_pokedex_lock`` inside ``save_pokemon`` -> ``mark_as_caught``).
    Blocking for the full 30s here would therefore stall the very thread we are
    waiting on. Failing fast and retrying with the lock released keeps both
    sides moving; ``save_item_evolution``'s loop owns the real deadline.
    """
    conn.execute(f"PRAGMA busy_timeout={_LOCK_ATTEMPT_TIMEOUT_MS};")
    try:
        yield
    finally:
        try:
            conn.execute(
                f"PRAGMA busy_timeout={getattr(db, '_BUSY_TIMEOUT_MS', 30000)};"
            )
        except Exception:
            # A connection too broken to restore its own PRAGMA is already
            # failing the caller through a louder error than this one.
            pass


@contextlib.contextmanager
def _immediate_transaction(conn):
    """``with conn:`` but with the write lock taken up front.

    ``ConnectionWrapper.__enter__`` issues a plain deferred ``BEGIN``. This
    block READS before it writes, so it would start on a SHARED lock and have
    to promote to RESERVED at the first UPDATE -- and sqlite deliberately does
    NOT run the busy handler on that promotion, because waiting there could
    deadlock two readers that each want to write. The 30s ``busy_timeout`` that
    ``_prepare_connection`` sets for exactly this situation (its comment cites
    mobile sync's bulk resolve holding one long write transaction) would be
    bypassed, and any concurrent writer would produce an instant "database is
    locked". ``BEGIN IMMEDIATE`` takes the write lock where the busy handler
    still applies.

    Drives ``conn._conn`` rather than the wrapper, exactly as
    ``__enter__``/``__exit__`` do: the wrapper's ``execute`` auto-heals a closed
    or malformed database by replaying onto a FRESH connection, which mid
    transaction would silently split this atomic write across two of them.
    """
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
        # Mirrors __exit__: never swallow a commit failure. A lost write that
        # reads as success is worse than a visible error.
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
    """Commit the Pokémon, one item charge, and caught/seen history together.

    ``expected_pokemon`` is the complete record read before any move dialogs.
    A changed/deleted record or missing item returns False without changing
    state. Database failures raise after rollback. This UI operation requires
    its own transaction: it must never commit a caller's deferred bulk writes
    or report success for an evolution that has not actually been committed.

    Lock contention is retried rather than reported: a concurrent writer (the
    bulk mobile resolve) means wait, not fail.
    """
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
            # Every write is rolled back before this propagates, so a retry is
            # safe: nothing was charged and nothing was evolved.
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
    """One all-or-nothing attempt. Raises on contention so the caller retries."""
    # Match the DB's history writer lock order. No callbacks or Qt work may
    # run while this lock/transaction is held.
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
    """The transaction itself, split out so the with-nesting stays readable."""
    # Obtain the cursor before BEGIN. Its execute() does not auto-heal or
    # replay a failed statement on another connection mid-transaction.
    # _immediate_transaction rather than ``with conn:`` so the SELECT below
    # cannot strand the write on a SHARED lock that sqlite refuses to promote.
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
            # After the charge, failure must leave through the rollback path;
            # returning False here would commit the item.
            raise RuntimeError("The Pokémon changed during item evolution")
        cursor.execute(
            "DELETE FROM items WHERE item_name = ? AND quantity = 0",
            (item_name,),
        )

        # save_pokemon/mark_as_caught commit independently, so write both
        # history keys here with the same cursor as the evolution.
        for key in ("pokedex_caught", "pokedex_seen"):
            cursor.execute("SELECT value FROM user_data WHERE key = ?", (key,))
            history = cursor.fetchone()
            try:
                existing = json.loads(history["value"]) if history else []
            except (TypeError, ValueError):
                # Match get_user_data / _coerce_pokedex_id_list, which both
                # treat a legacy or hand-edited value as recoverable rather than
                # fatal. An unreadable history list must not cost the player
                # their evolution; the two ids below are written back, and
                # _reconcile_pokedex_history restores the rest on next boot.
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
