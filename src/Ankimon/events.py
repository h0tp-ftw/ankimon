"""
events.py — a structured, machine-readable event stream for Ankimon.

Why this exists
---------------
Historically the only record of what the game *did* was free-text in
``app.log`` plus transient Qt tooltips/popups — fine for a human watching the
screen, useless for anything (a test, a dev console, an AI agent) that needs to
*observe outcomes programmatically*. This module is that observable channel: a
tiny event bus that every notable in-game action emits to (battle outcomes,
catches, faints, level-ups, evolutions, tooltips, sounds, dialogs, errors).

Design
------
- **Zero cost in production.** Disabled by default, so ``emit()`` is a single
  boolean check and returns. Nothing is captured until something calls
  ``enable()`` (the agent harness, or an opt-in dev console).
- **No Anki dependency.** Deliberately imports nothing from ``aqt``/``anki``/
  ``PyQt6`` so it is safe to import from the aqt-free core and from a plain
  ``python3`` process.

Usage
-----
Enable capture (optionally tee every event to a JSONL sink)::

    from .events import events
    events.enable(sink=lambda e: jsonl_file.write(json.dumps(e) + "\\n"))

Emit from anywhere in the game logic::

    from .events import events
    events.emit("catch", pokemon="Rattata", id=19, shiny=False)

Read + clear what happened since the last drain (what the harness does after
each action)::

    new_events = events.drain()
"""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional


class _EventBus:
    """In-memory, append-only event buffer. One shared instance: ``events``."""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._buffer: List[dict] = []
        self._sink: Optional[Callable[[dict], None]] = None
        self._seq: int = 0

    # --- lifecycle ---------------------------------------------------------

    def enable(self, sink: Optional[Callable[[dict], None]] = None) -> None:
        """Start capturing events. ``sink``, if given, is called with each
        event dict as it is emitted (e.g. to append a line to a JSONL file)."""
        self._enabled = True
        self._sink = sink

    def disable(self) -> None:
        """Stop capturing and detach any sink (back to zero-overhead state)."""
        self._enabled = False
        self._sink = None

    def is_enabled(self) -> bool:
        return self._enabled

    # --- producing ---------------------------------------------------------

    def emit(self, type: str, **fields: Any) -> None:
        """Record one event. No-op (and effectively free) unless enabled.

        Every event carries a monotonic ``seq`` and a wall-clock ``ts`` so a
        consumer can order and time them. ``fields`` should be JSON-serialisable
        so the stream can be written to JSONL / sent over the REPL verbatim.
        """
        if not self._enabled:
            return
        self._seq += 1
        evt = {"seq": self._seq, "ts": time.time(), "type": type}
        evt.update(fields)
        self._buffer.append(evt)
        if self._sink is not None:
            # A misbehaving sink must never break the game loop.
            try:
                self._sink(evt)
            except Exception:
                pass

    # --- consuming ---------------------------------------------------------

    def drain(self) -> List[dict]:
        """Return all buffered events and clear the buffer."""
        out = self._buffer
        self._buffer = []
        return out

    def peek(self) -> List[dict]:
        """Return a copy of the buffered events without clearing them."""
        return list(self._buffer)

    def clear(self) -> None:
        """Drop buffered events but keep the sequence counter."""
        self._buffer.clear()

    def reset(self) -> None:
        """Drop buffered events and reset the sequence counter (test isolation)."""
        self._buffer.clear()
        self._seq = 0


# The single shared event bus. Import this, not the class.
events = _EventBus()
