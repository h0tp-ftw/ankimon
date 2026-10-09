"""
ui_port.py — the UI presenter port (aqt-free).

The core game logic needs to, occasionally, *ask the user something* (which
move to use, which move to overwrite when learning a 5th) or *report a problem*.
Historically it did that by constructing Qt dialogs and calling ``showWarning``
inline, which is exactly what made the logic impossible to run without Anki.

This module defines the seam. The logic talks to ``services.ui`` — a presenter
object — instead of importing Qt dialogs. There are two implementations:

  * :class:`HeadlessPresenter` (here): no GUI. It records each interaction to
    the :mod:`events` stream and returns a scripted / policy-driven answer.
    It is the safe default (``services.ui`` is one of these until production
    swaps in the Qt one) and it is what the agent harness drives.
  * ``QtPresenter`` (in :mod:`gui_presenter`): shows the real
    ``MoveSelectionDialog`` / ``AttackDialog`` and ``show_warning_with_traceback``.
    The production composition root wires it in.

Pure *output* side effects (tooltips, sounds, info popups) are NOT routed
through here — those are made self-adapting at their own call sites (they emit
an event and render only if Qt is available). This port is only for the few
interactions that need an *answer back* or that report an error.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .events import events


class HeadlessPresenter:
    """Default, GUI-less presenter: records intent, returns scripted answers.

    The agent harness reads/sets the ``*_policy`` / ``next_move`` attributes to
    steer the few interactive moments without any dialog ever appearing.
    """

    def __init__(self) -> None:
        # Move chosen for the next attack, when "choose your move" is enabled.
        # None means "let the caller use its own default" (a random move).
        self.next_move: Optional[str] = None
        # What to do when a Pokemon would learn a 5th move:
        #   "reject" -> discard the new move (keep existing 4)
        #   "first"  -> overwrite the first existing move
        #   <name>   -> overwrite that specific move
        self.replace_policy: str = "reject"

    # --- questions (return a value) ---------------------------------------

    def choose_move(self, attacks: Sequence[str]) -> Optional[str]:
        """Pick which of ``attacks`` to use this turn. None → caller decides."""
        events.emit(
            "dialog",
            dialog="choose_move",
            options=list(attacks),
            chosen=self.next_move,
        )
        return self.next_move

    def choose_attack_to_replace(
        self, attacks: Sequence[str], new_attack: str
    ) -> Optional[str]:
        """Pick which existing move to overwrite with ``new_attack``.

        Returns the name of the move to replace, or None to discard the new
        move (keep the current set).
        """
        attacks = list(attacks)
        chosen: Optional[str] = None
        if self.replace_policy == "first" and attacks:
            chosen = attacks[0]
        elif self.replace_policy not in ("reject", "first"):
            chosen = self.replace_policy
        events.emit(
            "dialog",
            dialog="replace_attack",
            options=attacks,
            new_attack=new_attack,
            chosen=chosen,
        )
        return chosen

    def choose_moveset(
        self, pokemon_name: str, attacks: Sequence[str], new_attacks: Sequence[str]
    ) -> Optional[list]:
        """Pick which moves to keep from ``attacks`` plus ``new_attacks``.

        One dialog per Pokemon per battle, used when an XP Share recipient
        learns several moves at once. Headless, ``replace_policy`` drives the
        answer like :meth:`choose_attack_to_replace`: ``"reject"`` keeps the
        current set, ``"first"`` overwrites from the first slot onward, and a
        move name overwrites that one slot with the first new move.

        Parameters
        ----------
        pokemon_name : str
            Display name of the Pokemon learning the moves.
        attacks : Sequence[str]
            Raw move ids the Pokemon currently knows.
        new_attacks : Sequence[str]
            Raw move ids newly available from the levels just gained.

        Returns
        -------
        list of str or None
            Exactly 4 unique raw move ids drawn from ``attacks`` +
            ``new_attacks``, or None to keep the current set and discard
            every new move. Anything else is treated as None by the caller.
        """
        attacks = list(attacks)
        new_attacks = list(new_attacks)
        chosen: Optional[list] = None
        if self.replace_policy == "first" and attacks:
            chosen = list(attacks)
            for index, new_attack in enumerate(new_attacks[: len(chosen)]):
                chosen[index] = new_attack
        elif self.replace_policy not in ("reject", "first"):
            if self.replace_policy in attacks and new_attacks:
                chosen = list(attacks)
                chosen[chosen.index(self.replace_policy)] = new_attacks[0]
        events.emit(
            "dialog",
            dialog="choose_moveset",
            pokemon=pokemon_name,
            options=attacks,
            new_attacks=new_attacks,
            chosen=chosen,
        )
        return chosen

    # --- notifications (no return) ----------------------------------------

    def notify(self, level: str, message: str) -> None:
        """A would-be info/warning/error popup. Recorded, not shown."""
        events.emit("notify", level=level, message=message)

    def warn(self, message: str) -> None:
        """A would-be warning popup."""
        events.emit("notify", level="warning", message=message)

    def report_error(self, exception: BaseException, message: str = "") -> None:
        """A would-be error dialog with traceback."""
        events.emit("error", message=message, exception=repr(exception))
