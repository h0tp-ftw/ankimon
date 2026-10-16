"""Least-privilege QWebChannel registration for the unified web shell.

Each shell page receives the navigation bridge plus only the feature bridge it
uses.  Keeping this policy free of Qt makes the security boundary cheap to test
without booting Anki or a web engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCREEN_BRIDGE_NAMES = {
    "items": ("bridge", "nav"),
    "ankidex": ("nav",),
    "settings": ("settings", "nav"),
    "profile": ("trainer", "nav"),
    "team": ("team", "nav"),
    "mobile": ("mobile", "nav"),
    "history": ("mobile", "nav"),
    "backup_manager": ("backup", "nav"),
}


def register_screen_bridges(
    channel: Any, screen_id: str, available_bridges: Mapping[str, Any]
) -> tuple[str, ...]:
    """Register only the bridge objects required by ``screen_id``.

    Unknown screens are rejected instead of receiving a permissive default. A
    missing required bridge is likewise surfaced during shell construction.
    The returned names are useful for diagnostics and focused tests.
    """

    try:
        names = SCREEN_BRIDGE_NAMES[screen_id]
    except KeyError as exc:
        raise ValueError(f"unknown web-shell screen: {screen_id!r}") from exc

    missing = tuple(name for name in names if name not in available_bridges)
    if missing:
        raise KeyError(
            f"missing bridge(s) for web-shell screen {screen_id!r}: "
            f"{', '.join(missing)}"
        )

    for name in names:
        channel.registerObject(name, available_bridges[name])
    return names
