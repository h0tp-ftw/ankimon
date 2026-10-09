"""Party (active team) membership helpers shared by the Pokémon PC.

The six-slot team lives in the ``team`` table of :class:`AnkimonDB` and is the
single source of truth read by the Deck Browser overview grid, the reviewer
team-cycling hotkey, XP Share, mobile sync and the web Team screen. This module
is the aqt-free write seam the Pokémon PC uses to add and remove members, so
the ordering and capacity rules live in one place.

Nothing here imports Qt. Feedback goes back to the caller as a
:class:`TeamChange` whose ``outcome`` the UI maps to a translated message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..services import services

#: Maximum number of Pokémon in the party, mirroring the main-series games and
#: ``ankimon_profile_web.profile_data.MAX_TEAM_SIZE``.
MAX_TEAM_SIZE = 6


@dataclass(frozen=True)
class TeamChange:
    """Result of an add or remove request against the party.

    Parameters
    ----------
    ok : bool
        True when the team table was rewritten.
    outcome : str
        One of ``"added"``, ``"removed"``, ``"team_full"``, ``"already_on_team"``,
        ``"not_on_team"``, ``"missing_pokemon"`` or ``"db_error"``.
    slot : int, optional
        1-based slot the Pokémon occupies after the change (``"added"`` and
        ``"already_on_team"`` outcomes), otherwise ``None``.
    pruned : int
        Number of stale team rows (ids with no matching Pokémon) dropped while
        rewriting. Zero when nothing was rewritten.
    """

    ok: bool
    outcome: str
    slot: Optional[int] = None
    pruned: int = 0


def _log(level: str, message: str, logger=None) -> None:
    """Log through ``logger`` or the shared service logger, never raising.

    Parameters
    ----------
    level : str
        Log level understood by ``ShowInfoLogger.log``.
    message : str
        Text to record.
    logger : object, optional
        Explicit logger; defaults to ``services.logger`` when ``None``.
    """
    target = logger if logger is not None else services.logger
    if target is None:
        return
    try:
        target.log(level, message)
    except Exception:
        pass


def get_team_ids(db) -> list[str]:
    """Return the ordered individual ids stored in the team table.

    Parameters
    ----------
    db : AnkimonDB
        Database to read from.

    Returns
    -------
    list of str
        Ids in slot order. Entries are not checked against the collection, so
        a released Pokémon's stale id is still returned (see
        :func:`load_party` for the resolved view).
    """
    try:
        rows = db.get_team() or []
    except Exception as e:
        _log("error", f"Could not read the team table: {e}")
        return []
    ids: list[str] = []
    for row in rows:
        ind_id = row.get("individual_id") if isinstance(row, dict) else None
        if ind_id:
            ids.append(str(ind_id))
    return ids


def _existing_ids(db, ids: list[str]) -> set[str]:
    """Return the subset of ``ids`` that still has a ``captured_pokemon`` row.

    Existence is checked by id alone, so an unreadable (corrupt) record still
    counts as present and is never pruned from the team by mistake.

    Parameters
    ----------
    db : AnkimonDB
        Database to query.
    ids : list of str
        Candidate individual ids.

    Returns
    -------
    set of str
        Ids with a matching row.
    """
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    cursor = db.execute(
        f"SELECT individual_id FROM captured_pokemon WHERE individual_id IN ({placeholders})",
        tuple(ids),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def load_party(db, logger=None) -> list[dict[str, Any]]:
    """Resolve the team to full Pokémon records in slot order.

    Parameters
    ----------
    db : AnkimonDB
        Database to read from.
    logger : object, optional
        Logger for skipped entries; defaults to ``services.logger``.

    Returns
    -------
    list of dict
        Records from ``captured_pokemon`` in slot order, at most
        :data:`MAX_TEAM_SIZE`. Ids that no longer resolve are skipped and
        logged; the team table itself is never modified here.
    """
    ids = get_team_ids(db)
    if not ids:
        return []
    try:
        rows = db.get_pokemons_by_individual_ids(ids) or []
    except Exception as e:
        _log("error", f"Could not load party members: {e}", logger)
        return []
    by_id = {
        str(p.get("individual_id")): p for p in rows if isinstance(p, dict)
    }
    party: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ind_id in ids:
        record = by_id.get(ind_id)
        if record is None:
            _log(
                "warning",
                f"Team slot references missing Pokémon {ind_id!r}; skipping it.",
                logger,
            )
            continue
        if ind_id in seen:
            continue
        seen.add(ind_id)
        party.append(record)
    return party[:MAX_TEAM_SIZE]


def _clean_team_ids(db, logger=None) -> tuple[list[str], int]:
    """Return the current team ids minus stale or duplicate entries.

    Parameters
    ----------
    db : AnkimonDB
        Database to read from.
    logger : object, optional
        Logger for the pruning notice.

    Returns
    -------
    tuple of (list of str, int)
        Clean ordered ids and the number of entries dropped.
    """
    ids = get_team_ids(db)
    existing = _existing_ids(db, ids)
    clean: list[str] = []
    seen: set[str] = set()
    for ind_id in ids:
        if ind_id in existing and ind_id not in seen:
            clean.append(ind_id)
            seen.add(ind_id)
    pruned = len(ids) - len(clean)
    if pruned:
        _log(
            "info",
            f"Dropping {pruned} stale team slot(s) that no longer match a Pokémon.",
            logger,
        )
    return clean, pruned


def _write_team(db, ids: list[str], logger=None) -> bool:
    """Persist ``ids`` as the whole team and refresh the trainer card.

    Parameters
    ----------
    db : AnkimonDB
        Database to write to.
    ids : list of str
        Ordered individual ids, already validated by the caller.
    logger : object, optional
        Logger for failures.

    Returns
    -------
    bool
        True when ``save_team`` succeeded.
    """
    team_data = [{"individual_id": ind_id} for ind_id in ids]
    try:
        db.save_team(team_data)
    except Exception as e:
        _log("error", f"Failed to save the party: {e}", logger)
        return False
    trainer_card = services.trainer_card
    if trainer_card is not None:
        try:
            trainer_card.reload_team()
        except Exception as e:
            _log(
                "warning", f"Party saved but the trainer card did not refresh: {e}", logger
            )
    return True


def add_to_party(db, individual_id, logger=None) -> TeamChange:
    """Append a Pokémon to the first free party slot.

    Parameters
    ----------
    db : AnkimonDB
        Database holding the collection and the team table.
    individual_id : str
        Id of the Pokémon to add.
    logger : object, optional
        Logger for diagnostics.

    Returns
    -------
    TeamChange
        ``"added"`` with the new 1-based ``slot`` on success; otherwise
        ``"missing_pokemon"``, ``"already_on_team"``, ``"team_full"`` or
        ``"db_error"`` and nothing is written.
    """
    ind_id = str(individual_id or "")
    if not ind_id:
        return TeamChange(False, "missing_pokemon")
    try:
        if not _existing_ids(db, [ind_id]):
            return TeamChange(False, "missing_pokemon")
        ids, pruned = _clean_team_ids(db, logger)
    except Exception as e:
        _log("error", f"Could not read the party before adding {ind_id!r}: {e}", logger)
        return TeamChange(False, "db_error")
    if ind_id in ids:
        return TeamChange(False, "already_on_team", slot=ids.index(ind_id) + 1)
    if len(ids) >= MAX_TEAM_SIZE:
        return TeamChange(False, "team_full")
    ids.append(ind_id)
    if not _write_team(db, ids, logger):
        return TeamChange(False, "db_error")
    return TeamChange(True, "added", slot=len(ids), pruned=pruned)


def remove_from_party(db, individual_id, logger=None) -> TeamChange:
    """Remove a Pokémon from the party, closing the gap it leaves.

    Parameters
    ----------
    db : AnkimonDB
        Database holding the team table.
    individual_id : str
        Id of the Pokémon to remove.
    logger : object, optional
        Logger for diagnostics.

    Returns
    -------
    TeamChange
        ``"removed"`` on success; ``"not_on_team"`` when the id is not in a
        slot; ``"db_error"`` when the table could not be read or written.
    """
    ind_id = str(individual_id or "")
    if not ind_id:
        return TeamChange(False, "not_on_team")
    try:
        ids, pruned = _clean_team_ids(db, logger)
    except Exception as e:
        _log("error", f"Could not read the party before removing {ind_id!r}: {e}", logger)
        return TeamChange(False, "db_error")
    if ind_id not in ids:
        return TeamChange(False, "not_on_team")
    ids = [other for other in ids if other != ind_id]
    if not _write_team(db, ids, logger):
        return TeamChange(False, "db_error")
    return TeamChange(True, "removed", pruned=pruned)
