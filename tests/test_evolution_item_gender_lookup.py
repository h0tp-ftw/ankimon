"""Regression: a failed Pokemon lookup must not disable the gender gate.

``ItemWindow.Check_Evo_Item`` reads the SELECTED Pokemon's record so the
``pokemon_evolution.csv`` gender gate can be applied to the right Pokemon
(Gallade needs a male Kirlia, Froslass a female Snorunt). The trap is that
``check_evolution_by_item`` treats ``gender=None`` as *unrestricted* — a
deliberate fail-open for legacy saves whose records predate the column.

So swallowing a lookup failure into ``gender = None`` silently reports "this
Pokemon has no gender" when the truth is "I could not read this Pokemon". A
busy database — a lock, a transient sqlite error — was therefore enough to
hand a **female** Kirlia the male-only Gallade, which is precisely the class of
bug this branch exists to close.

The two states must stay distinguishable:

* record read, no recognised gender -> fail open (legacy behavior preserved);
* record NOT read (raised, or came back as a non-dict) -> refuse the item.

Everything below the ``aqt`` line is the genuine module: the real
``check_evolution_by_item``, the real ``pokemon_evolution.csv`` and the real
``pokedex.json`` decide the verdicts, so a data or gate change is visible here
rather than mocked away.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from conftest import isolated_modules

_SRC = Path(__file__).parent.parent / "src"

# Kirlia + Dawn Stone is the canonical gender split: Gallade (475) is the only
# Dawn Stone target Kirlia has, and it is male-only.
_KIRLIA_ID = 281
_GALLADE_ID = 475
_DAWN_STONE = "dawn-stone"


def _synthetic_aqt_finder():
    """A meta-path finder that answers any ``aqt``/``aqt.*`` import.

    Anki is absent from the documented dev env (AGENTS.md installs only
    pytest/pytest-qt/PyQt6/markdown), but ``item_window`` reaches it through a
    chain — ``evolution_window``, ``pc_box``, ``starter_window`` — that imports
    a moving set of ``aqt`` submodules (``aqt.qt``, ``aqt.theme``, ...) and
    pulls a moving set of Qt names out of them.

    Enumerating that set is what rots: the previous generation of these tests
    ``pytest.skip``-ped when a stub fell behind, and so never ran anywhere but a
    developer box with Anki installed. A finder cannot fall behind. Real
    ``aqt.qt`` re-exports PyQt6, so each attribute resolves against the genuine
    PyQt6 modules first and only falls back to a MagicMock for the handful of
    Anki-only names (``theme_manager`` and friends) that nothing here calls.
    """
    import PyQt6.QtCore
    import PyQt6.QtGui
    import PyQt6.QtWidgets

    qt_modules = (PyQt6.QtWidgets, PyQt6.QtGui, PyQt6.QtCore)

    def build(name):
        module = types.ModuleType(name)
        module.__path__ = []  # every aqt.* name must remain importable

        def __getattr__(attr, _name=name):
            for qt_module in qt_modules:
                if hasattr(qt_module, attr):
                    return getattr(qt_module, attr)
            return mock.MagicMock(name=f"{_name}.{attr}")

        module.__getattr__ = __getattr__
        return module

    class Loader:
        def create_module(self, spec):
            return build(spec.name)

        def exec_module(self, module):
            pass

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "aqt" or fullname.startswith("aqt."):
                return importlib.util.spec_from_loader(
                    fullname, Loader(), is_package=True
                )
            return None

    return Finder()


@pytest.fixture(scope="module")
def item_window():
    """Import the real ``pyobj/item_window.py`` headlessly, once."""
    with isolated_modules("PyQt6", "aqt", "Ankimon"):
        for pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
            module = types.ModuleType(pkg)
            module.__path__ = [str(_SRC / pkg.replace(".", "/"))]
            module.__package__ = pkg
            sys.modules[pkg] = module

        finder = _synthetic_aqt_finder()
        sys.meta_path.insert(0, finder)
        try:
            import aqt

            aqt.mw = mock.MagicMock()
            try:
                module = importlib.import_module("Ankimon.pyobj.item_window")
            except Exception as e:
                # Deliberately NOT pytest.skip: a skip here reports green while
                # every test in this file silently stops running. PyQt6 is a
                # documented test dependency and aqt is supplied above, so an
                # import failure means something real broke.
                raise AssertionError(
                    f"item_window is no longer importable headlessly: {e!r}"
                ) from e
            yield module
        finally:
            sys.meta_path.remove(finder)


class _Logger:
    """Records what the player would have been shown."""

    def __init__(self):
        self.messages = []

    def log_and_showinfo(self, level, message):
        self.messages.append((level, message))

    def log(self, level, message):
        self.messages.append((level, message))


class _EvoWindow:
    """Stands in for ``EvoWindow`` — ``objectName`` keeps ``is_alive`` true.

    Answering ``is_alive`` for real matters: a dead window sends
    ``Check_Evo_Item`` into ``singletons.get_evo_window()``, which would build a
    genuine Qt window mid-test.
    """

    def __init__(self):
        self.offers = []

    def objectName(self):
        return "evo_window"

    def ask_pokemon_evo(self, individual_id, prevo_id, evo_id, item_name=None):
        self.offers.append((individual_id, prevo_id, evo_id, item_name))


class _Bag:
    """The slice of ``ItemWindow`` that ``Check_Evo_Item`` actually reads.

    Called unbound against this, so no QWidget is constructed for what is a
    lookup plus a gate.
    """

    def __init__(self):
        self.logger = _Logger()
        self.evo_window = _EvoWindow()


def _use_dawn_stone(item_window, get_pokemon, **kwargs):
    """Run the item on a Kirlia; ``get_pokemon`` drives the database seam."""
    bag = _Bag()
    db = mock.MagicMock()
    db.get_pokemon.side_effect = get_pokemon
    with (
        mock.patch.object(item_window, "services", mock.MagicMock(db=db)),
        mock.patch.object(
            item_window, "show_warning_with_traceback", mock.MagicMock()
        ) as warned,
    ):
        item_window.ItemWindow.Check_Evo_Item(
            bag, "kirlia-1", _KIRLIA_ID, _DAWN_STONE, **kwargs
        )
    # Nothing here should reach the crash dialog; if it does, the assertion the
    # caller is about to make would be true for the wrong reason.
    assert not warned.called, f"unexpected traceback dialog: {warned.call_args}"
    return bag, db


def _raises(_individual_id):
    raise RuntimeError("database is locked")


# --------------------------------------------------------------------------- #
# The regression itself.
# --------------------------------------------------------------------------- #
def test_a_failed_lookup_does_not_offer_a_gender_gated_evolution(item_window):
    """The bug: a raising ``get_pokemon`` used to read as "no gender".

    With the gender unknown the gate falls open, so the Dawn Stone offered
    Gallade to whatever Kirlia was selected — female ones included — and
    accepting it persisted the wrong species.
    """
    bag, _ = _use_dawn_stone(item_window, _raises)

    assert bag.evo_window.offers == []
    level, message = bag.logger.messages[-1]
    assert level == "error"
    assert "database is locked" in message


def test_a_missing_record_does_not_offer_a_gender_gated_evolution(item_window):
    """``get_pokemon`` returning nothing is the same unknown, without an exception."""
    for absent in (None, [], "", 0):
        bag, _ = _use_dawn_stone(item_window, lambda _id, r=absent: r)

        assert bag.evo_window.offers == [], absent
        assert bag.logger.messages[-1][0] == "error", absent


def test_a_record_without_a_gender_still_evolves(item_window):
    """The fail-open the fix must NOT take away.

    Saves that predate the gender column carry no ``gender`` key. Refusing them
    would strip working evolutions from every legacy collection, so a record we
    *did* read stays unrestricted.
    """
    bag, _ = _use_dawn_stone(item_window, lambda _id: {"name": "Kirlia"})

    assert bag.evo_window.offers == [("kirlia-1", _KIRLIA_ID, _GALLADE_ID, _DAWN_STONE)]


@pytest.mark.parametrize("junk", ["Genderless", "N", "", "x"])
def test_an_unrecognised_gender_still_evolves(item_window, junk):
    bag, _ = _use_dawn_stone(item_window, lambda _id, g=junk: {"gender": g})

    assert bag.evo_window.offers[0][2] == _GALLADE_ID


# --------------------------------------------------------------------------- #
# ...while the gate the branch added keeps working.
# --------------------------------------------------------------------------- #
def test_a_male_kirlia_is_offered_gallade(item_window):
    bag, _ = _use_dawn_stone(item_window, lambda _id: {"gender": "M"})

    assert bag.evo_window.offers == [("kirlia-1", _KIRLIA_ID, _GALLADE_ID, _DAWN_STONE)]


def test_a_female_kirlia_is_refused_gallade(item_window):
    bag, _ = _use_dawn_stone(item_window, lambda _id: {"gender": "F"})

    assert bag.evo_window.offers == []
    # Refused by the gate, not by a failed read — the player is told the item
    # does not apply rather than that something went wrong.
    assert bag.logger.messages[-1] == (
        "info",
        "This Pokemon does not need this item.",
    )


# --------------------------------------------------------------------------- #
# The caller-supplied record.
# --------------------------------------------------------------------------- #
def test_a_supplied_record_is_used_without_a_second_read(item_window):
    """The web bag already holds the record; passing it must skip the re-read.

    Two reads are not merely wasteful — the id and the gender would come from
    different snapshots.
    """
    bag, db = _use_dawn_stone(
        item_window, _raises, pokemon_data={"id": _KIRLIA_ID, "gender": "M"}
    )

    db.get_pokemon.assert_not_called()
    assert bag.evo_window.offers[0][2] == _GALLADE_ID


def test_a_supplied_record_is_gated_like_a_read_one(item_window):
    bag, db = _use_dawn_stone(
        item_window, _raises, pokemon_data={"id": _KIRLIA_ID, "gender": "F"}
    )

    db.get_pokemon.assert_not_called()
    assert bag.evo_window.offers == []
