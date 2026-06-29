"""
harness/driver.py — high-level agent actions over a headless session.

This is the agent-facing API. Each action calls the *same* game callables the
GUI buttons/hooks invoke (the now-aqt-free battle loop and encounter functions),
then returns the structured events that action produced. ``get_state()`` returns
a JSON-serialisable snapshot of the world. An agent (or a scenario, or the REPL
server) drives the game purely through these.

Usage:
    from harness.driver import Driver
    d = Driver(settings_overrides={"battle.cards_per_round": 1})
    d.answer("good")          # answer a card -> battle happens
    d.get_state()             # observe HP, collection, trainer, ...
"""

from __future__ import annotations

from .bootstrap import quiet
from .headless_env import start_session
from .state import snapshot, grade_for


class Driver:
    """Drives one headless Ankimon session."""

    def __init__(self, **kwargs):
        self.env = start_session(**kwargs)
        self.services = self.env.services
        self.events = self.env.events

    # --- core actions ------------------------------------------------------

    def answer(self, ease=3):
        """Answer the current card. Reproduces the reviewer_did_answer_card flow:
        update the tracker's grade/streak/multiplier, then run the battle loop.

        ``ease`` may be 1-4 or "again"/"hard"/"good"/"easy".
        """
        grade = grade_for(ease)
        with quiet():
            self.services.tracker.review(grade)
            self.env.on_review_card()
        return self.drain_events()

    def catch(self):
        """Catch the wild Pokemon (only works once it has fainted), then spawn
        the next encounter — same as the reviewer's catch shortcut."""
        s = self.services
        ep = s.enemy_pokemon
        if ep.hp < 1:
            with quiet():
                self.env.catch_pokemon(
                    ep, s.tracker, s.logger, "", self.env.collected_ids, s.achievements
                )
                self.env.new_pokemon(ep, s.test_window, s.tracker, s.reviewer)
        else:
            self.events.emit(
                "notify", level="info",
                message="You only catch a pokemon once it's fainted!",
            )
        return self.drain_events()

    def defeat(self):
        """Defeat the (fainted) wild Pokemon for XP, then spawn the next one —
        same as the reviewer's defeat shortcut."""
        s = self.services
        ep, mp = s.enemy_pokemon, s.main_pokemon
        if ep.hp < 1:
            with quiet():
                self.env.kill_pokemon(
                    mp, ep, s.evo_window, s.logger, s.achievements, s.trainer_card
                )
                self.env.new_pokemon(ep, s.test_window, s.tracker, s.reviewer)
        else:
            self.events.emit(
                "notify", level="info",
                message="Wild pokemon has to be fainted to defeat it!",
            )
        return self.drain_events()

    def encounter(self):
        """Force a brand-new (random) wild encounter."""
        s = self.services
        with quiet():
            self.env.new_pokemon(s.enemy_pokemon, s.test_window, s.tracker, s.reviewer)
        return self.drain_events()

    def set_enemy(self, spec=None, **kw):
        """Force a SPECIFIC wild encounter — for reproducing a reported bug head-on.

        Accepts a spec dict or keywords (see harness/fixtures.py for all fields):
            d.set_enemy(species="Golem", level=50, moves=["Earthquake"])
            d.set_enemy(id=94, ability="Levitate", shiny=True)
        The Pokemon is built from the game's own pokedex data, so only the fields
        you pin are overridden. Emits an ``encounter`` event like a real one.
        """
        from .fixtures import set_enemy as _set_enemy
        spec = {**(spec or {}), **kw}
        with quiet():
            _set_enemy(self.services, self.events, spec)
        return self.drain_events()

    def set_setting(self, key, value):
        with quiet():
            self.services.settings.set(key, value)
        return {"ok": True, "key": key, "value": self.services.settings.get(key)}

    def set_move(self, move):
        """Script the move chosen on the next turn (needs controls.allow_to_choose_moves)."""
        self.services.ui.next_move = move
        return {"ok": True, "next_move": move}

    def add_cash(self, amount):
        s = self.services
        cash = int(s.settings.get("trainer.cash") or 0) + int(amount)
        with quiet():
            s.settings.set("trainer.cash", cash)
        return {"ok": True, "cash": cash}

    def advance_time(self, days=0, hours=0, minutes=0, seconds=0):
        """Fast-forward the controllable clock (create the Driver with
        clock_start=datetime(...)). Drives day/night, daily resets, streaks."""
        from .clock import advance, is_installed, now
        if not is_installed():
            return {"error": "clock not installed; create the Driver with clock_start=datetime(...)"}
        advance(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return {"ok": True, "now": str(now())}

    def time_of_day(self):
        """Ankimon's day/night reading at the current (controllable) clock."""
        from Ankimon.functions.pokedex_functions import get_time_of_day
        return get_time_of_day()

    def buy_item(self, name, item_type=None):
        """Buy an item: check cash, deduct the catalogue price, add to inventory.

        This drives the shop's economic logic headlessly (the Qt shop window
        itself is not built in the harness).
        """
        from Ankimon.utils import get_item_price, give_item

        s = self.services
        try:
            price = get_item_price(name)
        except Exception:
            price = None
        cash = int(s.settings.get("trainer.cash") or 0)
        if price is None:
            self.events.emit("buy", item=name, ok=False, reason="unknown_item")
            return self.drain_events()
        price = int(price)
        if cash < price:
            self.events.emit(
                "buy", item=name, ok=False, reason="insufficient_funds",
                cash=cash, price=price,
            )
            return self.drain_events()
        with quiet():
            s.settings.set("trainer.cash", cash - price)
            give_item(name, item_type)
        self.events.emit("buy", item=name, ok=True, price=price, cash_left=cash - price)
        return self.drain_events()

    # --- observation -------------------------------------------------------

    def drain_events(self):
        """Return + clear every event produced since the last drain."""
        return self.events.drain()

    def get_state(self):
        """A JSON-serialisable snapshot of the world."""
        return snapshot(self.services)

    # --- REPL dispatch -----------------------------------------------------

    def act(self, action, **kwargs):
        """Dispatch a named action with kwargs (used by the JSON-line server)."""
        if action.startswith("_"):
            return {"error": f"unknown action {action!r}"}
        fn = getattr(self, action, None)
        if not callable(fn):
            return {"error": f"unknown action {action!r}"}
        try:
            return fn(**kwargs)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
