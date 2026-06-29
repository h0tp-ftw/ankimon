"""
harness/real_driver.py — drive the REAL (Tier-2) Ankimon by firing real hooks.

Same action surface as the Tier-1 Driver, but instead of calling the game
functions directly it boots the genuine add-on (harness/real_env.py) and drives
it the way Anki does: firing the real ``gui_hooks`` and invoking the real
reviewer catch/defeat shortcuts. Every real window (TestWindow, PokemonPC,
EvoWindow, …) is live and doing its real work offscreen, so this is the faithful
path — real widgets, real memory, real glitches.

Requires the Tier-2 environment (see harness/setup_tier2.sh / real_env.py).
"""

from __future__ import annotations

from types import SimpleNamespace

from .bootstrap import quiet
from .real_env import start_real_session, _seed_assets
from .state import snapshot, normalize_ease


class RealDriver:
    """Drives the genuine add-on (real Qt windows) by firing real Anki hooks."""

    def __init__(self, settings_overrides=None, first_encounter=True, **kw):
        self.env = start_real_session(settings_overrides=settings_overrides, **kw)
        self.services = self.env.services
        self.events = self.env.events
        self.gui_hooks = self.env.gui_hooks
        self.aqt = self.env.aqt

        import Ankimon.functions.encounter_functions as ef
        import Ankimon.reviewer_ui as rui
        self._ef = ef
        self._rui = rui

        # Seed the placeholder sprite now (post-boot), so the real windows have a
        # non-null pixmap to render during play.
        _seed_assets(self.env.user_path)

        if first_encounter:
            # Open on a real, level-scaled wild encounter (the temp profile has no
            # sprites, so the real startup skips its own first-encounter step).
            with quiet():
                ef.new_pokemon(
                    self.services.enemy_pokemon, self.services.test_window,
                    self.services.tracker, self.services.reviewer,
                )

    # --- internals ---------------------------------------------------------

    def _reviewer_card(self):
        card = SimpleNamespace(id=1, time_taken=lambda: 1500)
        reviewer = SimpleNamespace(
            mw=self.aqt.mw, web=self.aqt.mw.reviewer.web, card=card,
        )
        return reviewer, card

    # --- actions -----------------------------------------------------------

    def answer(self, ease=3):
        """Answer a card by firing exactly the hooks Anki fires, in order:
        reviewer_will_answer_card -> reviewer_did_answer_card. That drives the
        real card_hooks (grade/streak), the real battle loop, and the real HUD."""
        ease = normalize_ease(ease)
        reviewer, card = self._reviewer_card()
        with quiet():
            self.gui_hooks.reviewer_will_answer_card(True, reviewer, card)
            self.gui_hooks.reviewer_did_answer_card(reviewer, card, ease)
        return self.drain_events()

    def catch(self):
        """Invoke the real reviewer catch shortcut (same code path as the key)."""
        with quiet():
            self._rui.catch_shortcut_function()
        return self.drain_events()

    def defeat(self):
        """Invoke the real reviewer defeat shortcut."""
        with quiet():
            self._rui.defeat_shortcut_function()
        return self.drain_events()

    def encounter(self):
        s = self.services
        with quiet():
            self._ef.new_pokemon(s.enemy_pokemon, s.test_window, s.tracker, s.reviewer)
        return self.drain_events()

    def set_setting(self, key, value):
        with quiet():
            self.services.settings.set(key, value)
        return {"ok": True, "key": key, "value": self.services.settings.get(key)}

    def advance_time(self, days=0, hours=0, minutes=0, seconds=0):
        """Fast-forward the controllable clock (create with clock_start=datetime(...))."""
        from .clock import advance, is_installed, now
        if not is_installed():
            return {"error": "clock not installed; create the RealDriver with clock_start=datetime(...)"}
        advance(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return {"ok": True, "now": str(now())}

    def time_of_day(self):
        from Ankimon.functions.pokedex_functions import get_time_of_day
        return get_time_of_day()

    # --- observation -------------------------------------------------------

    def drain_events(self):
        return self.events.drain()

    def get_state(self):
        return snapshot(self.services)

    def act(self, action, **kwargs):
        if action.startswith("_"):
            return {"error": f"unknown action {action!r}"}
        fn = getattr(self, action, None)
        if not callable(fn):
            return {"error": f"unknown action {action!r}"}
        try:
            return fn(**kwargs)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
