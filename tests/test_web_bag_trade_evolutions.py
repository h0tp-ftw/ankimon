"""Regression: the web bag's Pokemon picker must offer trade-with-item evolutions.

The trap this guards against: ``AnkimonItemsWeb.get_pokemon_choices`` re-implements
the evolution-eligibility test inline (deliberately — it runs per-Pokemon and must
stay free of file I/O) instead of calling ``check_evolution_by_item``. The two
copies drifted: the canonical helper accepts ``evoType`` in ``("useItem", "trade")``
while the picker's copy only accepted ``"useItem"``, even though the comment above
it claims to mirror the canonical logic.

``shop.js`` filters the picker to ``c.e === 1`` for evolution items, so an unflagged
Pokemon does not merely sort lower — it vanishes from the list. That made every
trade-with-held-item evolution unusable from the bag (reported as "Rhydon doesn't
appear when I click the Protector"), while the Protector itself was still consumed
and refunded, so the item looked broken rather than the picker.

The final test pins the two implementations to each other, so a future edit to one
that is not mirrored in the other fails here rather than in a player's save.
"""

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import isolated_modules

_SRC = Path(__file__).parent.parent / "src"
_POKEDEX = json.loads(
    (_SRC / "Ankimon" / "data_files" / "pokedex.json").read_text(encoding="utf-8")
)


def _normalize(name):
    """Strip the punctuation that separates pokedex display names from keys."""
    return (
        (name or "")
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
        .replace(":", "")
    )


def _trade_item_species():
    """Every plain-form species that evolves from a trade-with-held-item.

    Read out of the real ``pokedex.json`` rather than hard-coded, so a data update
    that adds one automatically widens the sweep. Regional forms are skipped: their
    eligibility depends on ``misc.active_region``, which is a separate branch.
    """
    cases = []
    for evo_key, evo in _POKEDEX.items():
        if evo.get("evoType") != "trade" or not evo.get("evoItem"):
            continue
        if evo.get("evoRegion"):
            continue
        prevo = _POKEDEX.get(_normalize(evo.get("prevo")))
        if not prevo:
            continue
        prevo_id = prevo.get("actual_id") or prevo.get("species_id")
        # pokedex.json spells items for display ("Deep Sea Scale", "King's Rock");
        # the bag passes items.csv identifiers ("deep-sea-scale", "kings-rock").
        item_identifier = evo["evoItem"].lower().replace("'", "").replace(" ", "-")
        cases.append(
            pytest.param(prevo.get("name"), prevo_id, item_identifier, id=f"{evo_key}")
        )
    return cases


TRADE_ITEM_SPECIES = _trade_item_species()


def _install_qt_stubs():
    """Install the Qt names ``shop_obj`` imports at module top.

    Two separate obstacles, both of which used to land in a ``pytest.skip`` — so
    this whole file, the Rhydon/Protector regression included, quietly never
    executed anywhere but a developer box with Anki installed:

    * ``shop_obj`` does ``from aqt import QDialog, ... QWebEngineView`` and
      subclasses those. Anki is absent from every CI run (AGENTS.md installs
      only pytest/pytest-qt/PyQt6/markdown). Plain empty classes are enough —
      nothing here constructs a widget; the tests call ``get_pokemon_choices``
      unbound against ``_StubHost``.
    * Earlier test modules replace ``PyQt6`` with a ``MagicMock``, which turns
      ``from PyQt6.QtWebChannel import QWebChannel`` into "PyQt6 is not a
      package".

    Clearing and restoring both namespaces is ``isolated_modules``' job around
    the caller, so this only installs; it never has to clean up.
    """
    from unittest.mock import MagicMock

    aqt = types.ModuleType("aqt")
    aqt.__path__ = []
    for cls_name in ("QDialog", "QVBoxLayout", "QWebEngineView", "QWebEnginePage"):
        setattr(
            aqt,
            cls_name,
            type(cls_name, (), {"__init__": lambda self, *a, **k: None}),
        )
    aqt.mw = MagicMock()

    aqt_qt = types.ModuleType("aqt.qt")
    for name in ("Qt", "QUrl", "QFrame", "QWebEngineProfile"):
        setattr(aqt_qt, name, MagicMock())

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.tooltip = lambda *a, **k: None
    aqt_utils.showInfo = lambda *a, **k: None

    sys.modules["aqt"] = aqt
    sys.modules["aqt.qt"] = aqt_qt
    sys.modules["aqt.utils"] = aqt_utils


@pytest.fixture(scope="module")
def shop_obj():
    """Import the real web-shell host once, headlessly.

    The module under test is the GENUINE one — the pokedex cache, items.csv and
    the CP formula are all loaded for real so the tests exercise the shipped
    data, and the ``services`` seam is swapped per-test with an auto-reverting
    ``patch.object``.

    What is faked is only ``shop_obj``'s Qt import surface, and only because
    Anki is absent headlessly: :func:`_install_qt_stubs` supplies the four
    ``aqt`` names the module subclasses, while ``isolated_modules`` drops any
    mocked ``PyQt6`` so the genuine package loads — and restores ``sys.modules``
    EXACTLY on the way out, including removing the synthetic ``aqt`` modules and
    the PyQt6 submodules imported inside. Without that last part a later test
    that installs a mocked ``PyQt6`` parent would inherit genuine children such
    as ``PyQt6.QtWebChannel`` from here, which is order-dependent and miserable
    to debug.

    **If you add a Qt import to shop_obj.py, extend `_install_qt_stubs` to
    match.** An import failure here is a hard error, not a skip: PyQt6 is a
    documented test dependency and ``aqt`` is supplied by this fixture, so
    nothing is left that an unimportable module could legitimately mean except
    that the stub set has drifted. Skipping instead is how all 31 of these tests
    went years without running anywhere but a developer box with Anki installed.
    """
    for pkg in (
        "Ankimon",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "Ankimon.ankimon_items_web",
    ):
        mod = sys.modules.get(pkg)
        if mod is None or not hasattr(mod, "__path__"):
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
            mod.__package__ = pkg
            sys.modules[pkg] = mod

    with isolated_modules(
        "PyQt6", "aqt", extra=("Ankimon.ankimon_items_web.shop_obj",)
    ):
        _install_qt_stubs()
        try:
            module = importlib.import_module("Ankimon.ankimon_items_web.shop_obj")
        except Exception as e:
            # Deliberately NOT pytest.skip: see the docstring. A skip here
            # reports green while every test in this file silently stops
            # running.
            raise AssertionError(
                "shop_obj is no longer importable with this file's Qt stubs — "
                "extend _install_qt_stubs() to cover its new imports (rather "
                f"than letting these tests silently skip). Original error: {e!r}"
            ) from e

        yield module


class _StubHost:
    """The slice of ``AnkimonItemsWeb`` that ``get_pokemon_choices`` actually reads.

    Calling the method unbound against this avoids constructing a QDialog (and a
    QWebEngineView) for what is a pure data transform.
    """

    _pokemon_choices_cache = None
    item_window = None  # no active Pokemon; nothing gets the "m" flag

    def _categorize(self, item_name, is_tm):
        return "evolution"


def _stub_services(rows):
    class _DB:
        @staticmethod
        def get_all_pokemon():
            return rows

    class _Services:
        db = _DB
        settings = None  # -> no active region
        logger = None

    return _Services


def _pokemon(name, pokedex_id):
    """A collection row with the fields the picker reads (and nothing else)."""
    return {
        "individual_id": f"iid-{name}-{pokedex_id}",
        "id": pokedex_id,
        "name": name,
        "nickname": "",
        "level": 40,
        "base_stats": {"hp": 50, "atk": 50, "def": 50, "spa": 50, "spd": 50, "spe": 50},
        "iv": {},
        "ev": {},
    }


def _choices(shop_obj, rows, item_name):
    with patch.object(shop_obj, "services", _stub_services(rows)):
        result = shop_obj.AnkimonItemsWeb.get_pokemon_choices(_StubHost(), item_name)
    return {c["id"]: c for c in result["choices"]}


def test_rhydon_is_offered_the_protector(shop_obj):
    """The reported bug: Rhydon + Protector, the picker's list came back empty."""
    rhydon = _pokemon("Rhydon", 112)
    choices = _choices(shop_obj, [rhydon], "protector")

    assert choices[rhydon["individual_id"]].get("e") == 1


def test_use_item_evolutions_are_still_offered(shop_obj):
    """The other branch of the predicate must keep working (Gloom + Leaf Stone)."""
    gloom = _pokemon("gloom", 44)
    choices = _choices(shop_obj, [gloom], "leaf-stone")

    assert choices[gloom["individual_id"]].get("e") == 1


def test_wrong_item_is_not_offered(shop_obj):
    """Eligibility is still per-item: Rhydon does not evolve with a Leaf Stone."""
    rhydon = _pokemon("Rhydon", 112)
    choices = _choices(shop_obj, [rhydon], "leaf-stone")

    assert "e" not in choices[rhydon["individual_id"]]


def test_species_without_an_item_evolution_is_not_offered(shop_obj):
    """A Pokemon with no item evolution at all stays unflagged."""
    pikachu = _pokemon("pikachu", 25)
    choices = _choices(shop_obj, [pikachu], "protector")

    assert "e" not in choices[pikachu["individual_id"]]


@pytest.mark.parametrize("prevo_name,prevo_id,item_identifier", TRADE_ITEM_SPECIES)
def test_picker_agrees_with_canonical_helper(
    shop_obj, prevo_name, prevo_id, item_identifier
):
    """Both implementations must accept every trade-with-item evolution.

    Ankimon has no trading, so these species are evolved by applying the held item
    directly — ``check_evolution_by_item`` has always resolved them. Asserting the
    picker against the helper (rather than against a hard-coded list) is what makes
    a future one-sided edit fail here.
    """
    item_id = shop_obj.return_id_for_item_name(item_identifier)
    assert item_id, f"{item_identifier} is missing from items.csv"

    assert shop_obj.check_evolution_by_item(prevo_id, item_id), (
        f"canonical helper no longer resolves {prevo_name} + {item_identifier}"
    )

    mon = _pokemon(prevo_name, prevo_id)
    choices = _choices(shop_obj, [mon], item_identifier)

    assert choices[mon["individual_id"]].get("e") == 1, (
        f"web bag picker hides {prevo_name} when using {item_identifier}"
    )


# --------------------------------------------------------------------------- #
# Gender-gated item evolutions must agree with the canonical helper too.
#
# ``check_evolution_by_item`` learned the CSV ``gender_id`` gate (Gallade needs
# a male Kirlia, Froslass a female Snorunt), and ``Check_Evo_Item`` — which is
# what ``handle_use_with_target`` ultimately calls — now passes the selected
# Pokemon's gender. The picker's inline copy had to learn it as well: shop.js
# filters this list to ``e === 1``, so leaving it out means the bag offers the
# player exactly the Pokemon the use is about to refuse with "This Pokemon does
# not need this item."
# --------------------------------------------------------------------------- #
_DAWN_STONE = "dawn-stone"


def _gendered(name, pokedex_id, gender):
    mon = _pokemon(name, pokedex_id)
    mon["gender"] = gender
    return mon


def test_male_kirlia_is_offered_the_dawn_stone(shop_obj):
    mon = _gendered("Kirlia", 281, "M")
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    assert choices[mon["individual_id"]].get("e") == 1


def test_female_kirlia_is_not_offered_the_dawn_stone(shop_obj):
    # Gallade (475) is the only Dawn Stone target Kirlia has, and it is male-only.
    mon = _gendered("Kirlia", 281, "F")
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    assert "e" not in choices[mon["individual_id"]]


def test_female_snorunt_is_offered_the_dawn_stone(shop_obj):
    mon = _gendered("Snorunt", 361, "F")
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    assert choices[mon["individual_id"]].get("e") == 1


def test_male_snorunt_is_not_offered_the_dawn_stone(shop_obj):
    mon = _gendered("Snorunt", 361, "M")
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    assert "e" not in choices[mon["individual_id"]]


def test_missing_gender_keeps_the_historical_no_check_behavior(shop_obj):
    # Old saves without a stored gender must not lose access to the evolution.
    mon = _pokemon("Kirlia", 281)  # no "gender" key at all
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    assert choices[mon["individual_id"]].get("e") == 1


def test_genderless_value_degrades_to_no_check(shop_obj):
    for junk in ("Genderless", "N", "", "x"):
        mon = _gendered("Kirlia", 281, junk)
        choices = _choices(shop_obj, [mon], _DAWN_STONE)
        assert choices[mon["individual_id"]].get("e") == 1, junk


def test_ungated_item_evolution_ignores_gender(shop_obj):
    # A Thunder Stone on Pikachu carries no gender_id row; both sexes qualify.
    for gender in ("M", "F"):
        mon = _gendered("pikachu", 25, gender)
        choices = _choices(shop_obj, [mon], "thunder-stone")
        assert choices[mon["individual_id"]].get("e") == 1, gender


@pytest.mark.parametrize(
    "name,pokedex_id,gender,expected",
    [
        ("Kirlia", 281, "M", 475),
        ("Snorunt", 361, "F", 478),
        ("Kirlia", 281, "F", None),
        ("Snorunt", 361, "M", None),
    ],
)
def test_picker_agrees_with_canonical_helper_on_gender(
    shop_obj, name, pokedex_id, gender, expected
):
    """Pin the two implementations to each other on the gender branch as well."""
    item_id = shop_obj.return_id_for_item_name(_DAWN_STONE)
    assert item_id, "dawn-stone is missing from items.csv"

    helper = shop_obj.check_evolution_by_item(pokedex_id, item_id, gender=gender)
    assert helper == expected

    mon = _gendered(name, pokedex_id, gender)
    choices = _choices(shop_obj, [mon], _DAWN_STONE)
    flagged = choices[mon["individual_id"]].get("e") == 1

    assert flagged is (expected is not None), (
        f"picker and helper disagree for a {gender} {name}"
    )


# --------------------------------------------------------------------------- #
# The web bag already holds the record, so it hands it to Check_Evo_Item.
#
# ``handle_use_with_target`` reads the selected Pokemon to resolve the pre-evo's
# pokedex id. ``Check_Evo_Item`` then needs the same record's gender for the gate
# — but re-reading it costs a second query AND lets the id and the gender come
# from different snapshots. Worse, a failed re-read is indistinguishable from
# "this Pokemon has no gender", which is the fail-open that hands a female Kirlia
# the male-only Gallade (see tests/test_evolution_item_gender_lookup.py).
#
# Passing the record through is the fix; these pin the caller's half of it.
# --------------------------------------------------------------------------- #
class _UseHost:
    """The slice of ``AnkimonItemsWeb`` that ``handle_use_with_target`` reads."""

    def __init__(self, bag, item=None):
        self.item_window = bag
        self.profile_data = None
        self._pokemon_choices_cache = None
        self._item = item or {
            "name": _DAWN_STONE,
            "category": "evolution",
            "owned_quantity": 1,
        }

    def _find_serialized(self, item_name):
        return self._item if item_name == self._item["name"] else None

    def _invalidate_pokemon_cache(self):
        self._pokemon_choices_cache = None


class _RecordingBag:
    """Stands in for ``ItemWindow``, recording how Check_Evo_Item was called."""

    def __init__(self):
        self.calls = []

    def Check_Evo_Item(self, individual_id, prevo_id, item_name, pokemon_data=None):
        self.calls.append((individual_id, prevo_id, item_name, pokemon_data))


def _use_on(shop_obj, record):
    """Run the web bag's "use this item on that Pokemon" with one stored record."""
    reads = []

    def get_pokemon(individual_id):
        reads.append(individual_id)
        return record

    bag = _RecordingBag()
    services = types.SimpleNamespace(
        db=types.SimpleNamespace(get_pokemon=get_pokemon),
        settings=None,
        logger=None,
    )

    with patch.object(shop_obj, "services", services):
        result = shop_obj.AnkimonItemsWeb.handle_use_with_target(
            _UseHost(bag), _DAWN_STONE, "kirlia-1"
        )
    return result, bag, reads


def test_the_web_bag_hands_the_loaded_record_to_check_evo_item(shop_obj):
    record = {"id": 281, "gender": "F", "name": "Kirlia"}
    result, bag, reads = _use_on(shop_obj, record)

    assert result["ok"] is True
    assert bag.calls == [("kirlia-1", 281, _DAWN_STONE, record)]
    # Exactly one read: the gender arrives with the id, not from a second query.
    assert reads == ["kirlia-1"]
