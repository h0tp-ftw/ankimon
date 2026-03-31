import json
from functools import lru_cache
from aqt import mw
from .pyobj.translator import LANG_NUMBERS
from .resources import move_names_file_path


def _current_lang_code() -> str:
    try:
        lang_id = int(mw.settings_obj.get("misc.language"))
    except Exception:
        lang_id = 9  # Default to English on failure
    return LANG_NUMBERS.get(lang_id, "en")


@lru_cache(maxsize=None)
def _load_move_name_lookups(lang_code: str):
    """
    Load English move names and, if available, a localized set.
    Falls back to English when the localized file is missing or invalid.
    """
    with open(move_names_file_path, "r", encoding="utf-8") as f:
        base_lookup = json.load(f)

    localized_path = move_names_file_path.with_name(f"move_names_{lang_code}.json")
    try:
        with open(localized_path, "r", encoding="utf-8") as f:
            localized_lookup = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        localized_lookup = {}

    return base_lookup, localized_lookup


def format_move_name(move: str) -> str:
    """
    Look up the move name using the normalized key.
    Falls back to English then prettified name if not found.
    """
    lang_code = _current_lang_code()
    base_lookup, localized_lookup = _load_move_name_lookups(lang_code)
    key = move.replace(" ", "").replace("-", "").replace("_", "").lower()
    return (
        localized_lookup.get(key)
        or base_lookup.get(key)
        or " ".join(word.capitalize() for word in move.replace("_", " ").split())
    )
