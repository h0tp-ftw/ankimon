"""Backwards-compatible move-name / move-description helpers.

The actual localization now lives in :mod:`localized_text` (all categories, all
languages). This module keeps the historical ``format_move_name`` /
``format_move_description`` / ``_current_lang_code`` names other modules import,
and adds the English base-name lookup used as the final fallback.
"""
import json
from functools import lru_cache

from .resources import move_names_file_path
from .localized_text import (  # noqa: F401  (re-exported)
    current_lang_code as _current_lang_code,
    move_description as _localized_move_description,
    move_name as _localized_move_name,
    normalize_key as _normalize_move_key,
)


@lru_cache(maxsize=1)
def _english_move_names() -> dict:
    try:
        with open(move_names_file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def format_move_name(move: str) -> str:
    """Localized move name, falling back to the English name then a prettified key."""
    key = _normalize_move_key(move)
    english = _english_move_names().get(key)
    prettified = " ".join(w.capitalize() for w in move.replace("_", " ").split())
    return _localized_move_name(move, english or prettified)


def format_move_description(move: str, english_fallback: str = "") -> str:
    """Localized in-game move description, or ``english_fallback`` if unavailable."""
    return _localized_move_description(move, english_fallback)
