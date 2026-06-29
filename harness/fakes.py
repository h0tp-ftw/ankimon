"""
harness/fakes.py — recording stand-ins for the GUI windows.

Production wires ``services.test_window`` / ``evo_window`` / ``pokemon_pc`` /
``reviewer`` to real Qt windows. Headless, the harness wires these fakes: every
method call becomes a structured ``ui`` event (so the agent can see that a HUD
repaint / death screen / etc. *would* have happened) and otherwise does nothing.

The only fakes with real behavior are:
  * FakeEvoWindow.ask_pokemon_evo — applies the evolution policy (default:
    decline, so a ready-to-evolve Pokemon doesn't re-prompt every level) and
    exposes ``.translator`` (xp_share_gain_exp reads ``evo_window.translator``).
  * FakeReviewer.refresh_hud / update_life_bar — emit a ``hud`` event.

This module imports only the aqt-free event bus / registry — never aqt/PyQt6.
"""

from __future__ import annotations

from Ankimon.events import events
from Ankimon.services import services


class _RecordingFake:
    """Any attribute access returns a callable that records a ``ui`` event."""

    _target = "window"

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            events.emit("ui", target=self._target, method=name)
            return None
        return _record


class FakeTestWindow(_RecordingFake):
    """Stands in for TestWindow. display_battle/_item/_first_encounter/
    _pokemon_death all become recorded no-ops via the generic recorder."""

    _target = "test_window"


class FakePokemonPC(_RecordingFake):
    """Stands in for PokemonPC (refresh_pokemon_grid etc.)."""

    _target = "pokemon_pc"


class FakeReviewer(_RecordingFake):
    """Stands in for Reviewer_Manager. The core logic only calls refresh_hud()
    (and, historically, update_life_bar); both just record a hud event."""

    _target = "reviewer"

    def refresh_hud(self):
        events.emit("hud", action="refresh")

    def update_life_bar(self, reviewer=None, card=None, ease=None):
        events.emit("hud", action="update")


class FakeEvoWindow(_RecordingFake):
    """Stands in for EvoWindow.

    Policy controls what happens when an evolution is offered:
      * "decline" (default) — record the prompt and mark the Pokemon's
        ``evolution_rejected`` flag so it does not re-prompt on every level-up.
      * "ignore" — record the prompt and do nothing (it may re-prompt later).

    ("accept" — actually performing the evolution — lives in the Qt
    EvoWindow.evolve_pokemon; the headless harness records the offer instead of
    reimplementing that mutation. Extend here if you need headless evolves.)
    """

    _target = "evo_window"

    def __init__(self, policy: str = "decline"):
        self.policy = policy
        # xp_share_gain_exp reads evo_window.translator.translate(...).
        self.translator = services.translator

    def ask_pokemon_evo(self, individual_id, prevo_id, evo_id):
        events.emit(
            "evolution_prompt",
            individual_id=individual_id,
            prevo_id=prevo_id,
            evo_id=evo_id,
            policy=self.policy,
        )
        if self.policy == "decline":
            try:
                db = services.db
                pkmn = db.get_pokemon(individual_id)
                if pkmn:
                    pkmn["evolution_rejected"] = True
                    db.save_pokemon(pkmn)
            except Exception:
                pass
            mp = services.main_pokemon
            if mp is not None and getattr(mp, "individual_id", None) == individual_id:
                mp.evolution_rejected = True


def install_fakes(evolution_policy: str = "decline") -> None:
    """Register the recording fakes in the service registry (headless mode).

    Leaves ``services.ui`` as the default HeadlessPresenter and ``services.col``
    as None (so get_total_reviews returns 0 with no Anki collection).
    """
    services.populate(
        test_window=FakeTestWindow(),
        evo_window=FakeEvoWindow(policy=evolution_policy),
        pokemon_pc=FakePokemonPC(),
        reviewer=FakeReviewer(),
    )
