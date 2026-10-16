"""Generate localized in-game text for every category Ankimon displays, in every
language that has an official Pokémon localization.

Everything here comes straight from PokeAPI's CSV data dump
(github.com/PokeAPI/pokeapi, data/v2/csv/), which is the official in-game text
ripped from the games — NOT machine translation. Word-for-word translation of
these strings is wrong ("Struggle Bug" is "Estoicismo" in Spanish), so we pull
the real localizations once instead of hand-translating thousands of entries.

Outputs: src/Ankimon/data_files/i18n/<category>_<lang>.json
  categories: move_names move_desc ability_names ability_desc item_names
              item_desc type_names nature_names stat_names
  langs:      jp kr ch fr de sp es_latam it   (en is the base, never emitted)

Czech and Polish have no official Pokémon localization, so they are not emitted
and fall back to English at runtime.

Keys: the English identifier/name, lowercased with every non-alphanumeric
character removed ("Struggle Bug" / "struggle-bug" -> "strugglebug"). Runtime
lookups normalize their key the same way.

Usage:
    python scripts/generate_localized_text.py                 # fetch from GitHub
    python scripts/generate_localized_text.py /path/to/csvdir # use local CSVs
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "src" / "Ankimon" / "data_files" / "i18n"
CSV_BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"

LANG_EN = 9
# PokeAPI local_language_id -> Ankimon short code(s). ja uses id 1 (kana), which
# is how the games render names; id 11 is the kanji set.
PLANG_TO_CODES = {
    1: ["jp"],
    3: ["kr"],
    4: ["ch"],
    5: ["fr"],
    6: ["de"],
    7: ["sp", "es_latam"],
    8: ["it"],
}
WANTED_PLANGS = set(PLANG_TO_CODES)

_WS = re.compile(r"\s+")


def norm_key(value: str) -> str:
    return "".join(c for c in value.lower() if c.isalnum())


def clean_flavor(text: str) -> str:
    text = text.replace("­", "")            # soft hyphen
    text = text.replace(" ", " ").replace("　", " ")
    text = text.replace("\x0c", " ").replace("\n", " ").replace("\r", " ")
    return _WS.sub(" ", text).strip()


def load_csv(name: str, csvdir: Path | None) -> list[dict]:
    if csvdir is not None:
        text = (csvdir / name).read_text(encoding="utf-8")
    else:
        print(f"  fetching {name}")
        with urllib.request.urlopen(CSV_BASE + name, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def english_keys(id_rows: list[dict]) -> dict[int, str]:
    """entity id -> normalized english key, taken from the identifier column."""
    return {int(r["id"]): norm_key(r["identifier"]) for r in id_rows}


def _fk(rows: list[dict]) -> str:
    """Name/flavor CSVs key the entity as <thing>_id, not id."""
    for col in rows[0]:
        if col.endswith("_id") and col not in ("language_id", "version_group_id", "local_language_id"):
            return col
    raise KeyError("no entity id column")


def names_by_lang(name_rows: list[dict]) -> dict[int, dict[int, str]]:
    id_col = _fk(name_rows)
    out: dict[int, dict[int, str]] = {}
    for r in name_rows:
        lang = int(r["local_language_id"])
        if lang not in WANTED_PLANGS:
            continue
        out.setdefault(int(r[id_col]), {})[lang] = r["name"]
    return out


def flavor_by_lang(flavor_rows: list[dict]) -> dict[int, dict[int, str]]:
    """Newest version group's flavor text per entity per language."""
    id_col = _fk(flavor_rows)
    best: dict[tuple[int, int], tuple[int, str]] = {}
    for r in flavor_rows:
        lang = int(r["language_id"])
        if lang not in WANTED_PLANGS:
            continue
        eid = int(r[id_col])
        vg = int(r["version_group_id"])
        cur = best.get((eid, lang))
        if cur is None or vg > cur[0]:
            best[(eid, lang)] = (vg, r["flavor_text"])
    out: dict[int, dict[int, str]] = {}
    for (eid, lang), (_, txt) in best.items():
        out.setdefault(eid, {})[lang] = clean_flavor(txt)
    return out


def write_category(category: str, keys: dict[int, str], by_lang: dict[int, dict[int, str]]):
    # code -> {normkey: localized}
    per_code: dict[str, dict[str, str]] = {}
    for eid, key in keys.items():
        if not key:
            continue
        langs = by_lang.get(eid, {})
        for plang, value in langs.items():
            if not value:
                continue
            for code in PLANG_TO_CODES[plang]:
                per_code.setdefault(code, {})[key] = value

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for code, mapping in sorted(per_code.items()):
        path = OUT_DIR / f"{category}_{code}.json"
        path.write_text(
            json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=1)
            + "\n",
            encoding="utf-8",
        )
        print(f"  {path.relative_to(REPO_ROOT)}  ({len(mapping)})")


def do(category, id_csv, name_csv, flavor_csv, csvdir):
    print(f"{category}:")
    keys = english_keys(load_csv(id_csv, csvdir))
    if name_csv:
        write_category(f"{category}_names", keys, names_by_lang(load_csv(name_csv, csvdir)))
    if flavor_csv:
        write_category(f"{category}_desc", keys, flavor_by_lang(load_csv(flavor_csv, csvdir)))


def main() -> None:
    csvdir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print("Source:", csvdir or "PokeAPI GitHub")

    do("move", "moves.csv", "move_names.csv", "move_flavor_text.csv", csvdir)
    do("ability", "abilities.csv", "ability_names.csv", "ability_flavor_text.csv", csvdir)
    do("item", "items.csv", "item_names.csv", "item_flavor_text.csv", csvdir)
    do("type", "types.csv", "type_names.csv", None, csvdir)
    do("nature", "natures.csv", "nature_names.csv", None, csvdir)
    do("stat", "stats.csv", "stat_names.csv", None, csvdir)

    print("Done.")


if __name__ == "__main__":
    main()
