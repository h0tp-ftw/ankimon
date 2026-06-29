"""
harness/clock.py — controllable ("fast-forward") time for the harness.

Two different notions of "time" matter:

1. Real-time DELAYS (animations, tooltip timers, card timers). The harness never
   runs Qt's event loop continuously, so these are simply skipped — every action
   runs at full CPU speed (that's why 20k reviews take ~22s). There's nothing to
   speed up; the waiting just doesn't happen.

2. The CALENDAR (datetime.now / date.today). Ankimon reads the real wall clock
   for day/night evolutions, the daily cash reward, streaks, and capture stamps.
   By default that's always "now", so a 10k-turn run all happens on one date.

This module makes #2 controllable so an agent can SET or FAST-FORWARD the clock —
e.g. advance a day each in-game "day" of a long run to trigger day/night
evolutions, daily resets, and streak growth. It works by swapping in a fake
`datetime` module whose `datetime`/`date` are faithful SUBCLASSES of the real
ones (so isinstance, arithmetic, strftime all still work) with only
now/today/utcnow overridden to the controlled instant.

Install BEFORE importing any Ankimon module. Harness-only; nothing in src/ changes.

    from harness import clock
    clock.install_clock(datetime(2026, 6, 1, 12, 0))   # noon, June 1
    clock.advance(hours=10)                             # -> 22:00 (night)
    clock.advance(days=1)                              # next day (daily reset, streak)
"""

from __future__ import annotations

import sys
import types
import datetime as _real

_state = {"now": _real.datetime(2026, 1, 1, 12, 0, 0)}
_installed = False


class _FakeDateTime(_real.datetime):
    @classmethod
    def now(cls, tz=None):
        n = _state["now"]
        if tz is not None:
            # Treat the controlled instant as wall-clock time in the requested tz
            # (keeps .hour == the controlled hour, which is what day/night uses).
            return n.replace(tzinfo=tz)
        return n

    @classmethod
    def utcnow(cls):
        return _state["now"]

    @classmethod
    def today(cls):
        return _state["now"]


class _FakeDate(_real.date):
    @classmethod
    def today(cls):
        n = _state["now"]
        return _real.date(n.year, n.month, n.day)


def install_clock(start=None):
    """Swap in the controllable `datetime` module. Call before importing Ankimon."""
    global _installed
    if start is not None:
        _state["now"] = start
    fake = types.ModuleType("datetime")
    for name in dir(_real):
        if not name.startswith("_"):
            setattr(fake, name, getattr(_real, name))
    fake.datetime = _FakeDateTime
    fake.date = _FakeDate
    fake.__ankimon_fake_clock__ = True
    sys.modules["datetime"] = fake
    _installed = True
    return fake


def is_installed() -> bool:
    return _installed


def set_now(dt) -> "_real.datetime":
    _state["now"] = dt
    return _state["now"]


def advance(days=0, hours=0, minutes=0, seconds=0) -> "_real.datetime":
    _state["now"] = _state["now"] + _real.timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )
    return _state["now"]


def now() -> "_real.datetime":
    return _state["now"]
