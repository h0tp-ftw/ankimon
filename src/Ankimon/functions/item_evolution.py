"""Atomic persistence for an item evolution prepared by the UI."""

import json


def save_item_evolution(db, expected_pokemon, evolved_pokemon, item_name) -> bool:
    """Commit the Pokémon, one item charge, and caught/seen history together.

    ``expected_pokemon`` is the complete record read before any move dialogs.
    A changed/deleted record or missing item returns False without changing
    state. Database failures raise after rollback. This UI operation requires
    its own transaction: it must never commit a caller's deferred bulk writes
    or report success for an evolution that has not actually been committed.
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

    # Match the DB's history writer lock order. No callbacks or Qt work may
    # run while this lock/transaction is held.
    with db._pokedex_lock, db.lease_connection() as conn:
        if (
            conn.in_transaction
            or conn._disable_commit
            or getattr(conn, "_txn_depth", 0)
        ):
            raise RuntimeError("Item evolution requires its own transaction")

        # Obtain the cursor before BEGIN. Its execute() does not auto-heal or
        # replay a failed statement on another connection mid-transaction.
        with conn.cursor() as cursor, conn:
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
                "UPDATE captured_pokemon SET data = ? "
                "WHERE individual_id = ? AND data = ?",
                (evolved_data, individual_id, row["data"]),
            )
            if cursor.rowcount != 1:
                # After the charge, failure must leave through __exit__'s
                # rollback path; returning False here would commit the item.
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
                existing = json.loads(history["value"]) if history else []
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

    db._clear_reviewer_ownership_cache()
    return True
