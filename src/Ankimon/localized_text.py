"""Runtime lookups for localized in-game text.

Data files live in ``data_files/i18n/<category>_<lang>.json`` and are produced by
``scripts/generate_localized_text.py`` from PokeAPI's CSV dump (official game
text, not machine translation). Czech and Polish have no official Pokémon
localization, so those settings fall back to English here.

Categories: ``move_names move_desc ability_names ability_desc item_names
item_desc type_names nature_names stat_names``.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .resources import addon_dir
from .services import services
from .pyobj.translator import LANG_NUMBERS

_I18N_DIR = addon_dir / "data_files" / "i18n"


def current_lang_code() -> str:
    try:
        lang_id = int(services.settings.get("misc.language"))
    except Exception:
        lang_id = 9
    return LANG_NUMBERS.get(lang_id, "en")


def normalize_key(value: str) -> str:
    return "".join(c for c in str(value).lower() if c.isalnum())


@lru_cache(maxsize=None)
def _load(category: str, lang_code: str) -> dict:
    if lang_code == "en":
        return {}
    path = _I18N_DIR / f"{category}_{lang_code}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def localized(category: str, key: str, fallback: str = "", prettify: bool = True) -> str:
    """Localized string for ``key`` in ``category`` for the current language.

    Returns ``fallback`` when there is no localized entry — English users and
    untranslated entries are unaffected. If ``fallback`` is empty and
    ``prettify`` is set (names, not descriptions), a prettified ``key`` is
    returned instead of an empty string.
    """
    table = _load(category, current_lang_code())
    hit = table.get(normalize_key(key))
    if hit:
        return hit
    if fallback or not prettify:
        return fallback
    return " ".join(w.capitalize() for w in str(key).replace("-", " ").replace("_", " ").split())


# Convenience wrappers ------------------------------------------------------

def move_name(move: str, fallback: str = "") -> str:
    return localized("move_names", move, fallback)


def move_description(move: str, fallback: str = "") -> str:
    return localized("move_desc", move, fallback, prettify=False)


def ability_name(ability: str, fallback: str = "") -> str:
    return localized("ability_names", ability, fallback)


def ability_description(ability: str, fallback: str = "") -> str:
    return localized("ability_desc", ability, fallback, prettify=False)


def item_name(item: str, fallback: str = "") -> str:
    return localized("item_names", item, fallback)


def item_description(item: str, fallback: str = "") -> str:
    return localized("item_desc", item, fallback, prettify=False)


def type_name(type_: str, fallback: str = "") -> str:
    return localized("type_names", type_, fallback)


def type_list(types, separator: str = "/") -> str:
    if not types:
        return type_name("normal")
    return separator.join(type_name(t) for t in types)


def nature_name(nature: str, fallback: str = "") -> str:
    return localized("nature_names", nature, fallback)


def stat_name(stat: str, fallback: str = "") -> str:
    return localized("stat_names", stat, fallback)
