"""Regression tests for ``pyobj/item_window.py`` healing-item consumption.

``ItemWindow.Check_Heal_Item`` used to grant the HP first and only then call
``delete_item``, which threw away the database's answer:
``DatabaseManager.update_item_quantity`` logs "Item not found in inventory."
and returns without touching the bag when the row is missing.  Nothing between
the click and the heal ever checked that a potion was actually spent -- the Qt
bag's "Heal Mainpokemon" button (``ItemLabel``) binds ``Check_Heal_Item``
directly with no quantity check at all -- so any stale or repeated request
healed for free.  These pin the consume-before-heal ordering and the refusal.

They also pin what the review of that first fix turned up: the bag redraw must
not sit between the payment and the heal (a redraw that throws would spend the
potion and grant nothing), reading the quantity is not the same as paying for
the item (``DatabaseManager.consume_item`` decrements under a condition and
reports whether it matched), and the bag button must not dereference
``main_pokemon`` inside its own lambda.

``item_window.py`` imports Qt and most of the addon at module scope, so it is
loaded in isolation with those stubbed (mirroring
``test_evolution_item_consumption.py``), except ``Ankimon.services`` which is
the real aqt-free registry so ``services.db`` can be pointed at a fake.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


class _MockQWidget:
    """Real class so ``class ItemWindow(QWidget)`` is a valid definition."""

    def __init__(self, *args, **kwargs):
        pass


class _FakeItemDB:
    """The ``services.db`` seam: an inventory plus a write log.

    ``quantity_calls`` records every attempted write to the bag as
    ``(item_name, delta)``, whichever method made it, so a test can say "the
    bag was written exactly once, by -1" without caring which one.
    """

    def __init__(self, inventory=None):
        self.inventory = dict(inventory or {})
        self.quantity_calls = []

    def get_item(self, item_name):
        if item_name not in self.inventory:
            return None
        return {"item_name": item_name, "quantity": self.inventory[item_name]}

    def consume_item(self, item_name, count=1):
        """Mirror of the real atomic consume: decrement only if stock covers it.

        The real one is a single ``UPDATE ... WHERE quantity >= ?`` and reports
        whether the condition matched, so unlike ``update_item_quantity`` it can
        tell "you spent your last one" from "there was nothing to spend".
        """
        self.quantity_calls.append((item_name, -count))
        current = self.inventory.get(item_name, 0)
        if current < count:
            return False
        if current == count:
            self.inventory.pop(item_name, None)
        else:
            self.inventory[item_name] = current - count
        return True

    def update_item_quantity(self, item_name, delta):
        self.quantity_calls.append((item_name, delta))
        current = self.inventory.get(item_name, 0)
        if current == 0:
            return 0
        new = current + delta
        if new < 0:
            return current
        if new == 0:
            self.inventory.pop(item_name, None)
        else:
            self.inventory[item_name] = new
        return new


class _FakePokemon:
    def __init__(self, name="mewtwo", hp=10, max_hp=100):
        self.name = name
        self.hp = hp
        self.max_hp = max_hp
        self.current_hp = hp


def _force_load(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def item_window_mod():
    """Load the real ``item_window`` with a stubbed Qt/addon runtime."""
    parent_pkgs = ("Ankimon", "Ankimon.functions", "Ankimon.pyobj")
    stub_names = [
        "aqt", "aqt.utils", "aqt.qt",
        "PyQt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
        "Ankimon.pyobj.evolution_window",
        "Ankimon.pyobj.InfoLogger",
        "Ankimon.pyobj.pc_box",
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.pyobj.settings",
        "Ankimon.pyobj.starter_window",
        "Ankimon.pyobj.error_handler",
        "Ankimon.business",
        "Ankimon.functions.pokedex_functions",
        "Ankimon.functions.badges_functions",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.resources",
        "Ankimon.utils",
        "Ankimon.pyobj.item_window",
    ]
    saved = {
        name: sys.modules.get(name)
        for name in (
            *stub_names,
            *parent_pkgs,
            "Ankimon.services",
            # Not stubbed -- the real-database test below loads it for real, and
            # it must be put back exactly as it was found either way.
            "Ankimon.pyobj.database_manager",
        )
    }

    try:
        for pkg in parent_pkgs:
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
            mod.__package__ = pkg
            sys.modules[pkg] = mod
        for name in stub_names:
            sys.modules[name] = MagicMock()
        # ItemWindow subclasses QWidget, so that one must be a real class.
        sys.modules["PyQt6.QtWidgets"].QWidget = _MockQWidget
        # ``Ankimon.services`` stays real: it is the seam the fake DB rides in on.
        services_mod = _force_load("Ankimon.services", _SRC / "Ankimon" / "services.py")
        mod = _force_load(
            "Ankimon.pyobj.item_window", _SRC / "Ankimon" / "pyobj" / "item_window.py"
        )
        mod._services_mod = services_mod
        yield mod
    finally:
        for name, val in saved.items():
            if val is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = val


def _make_window(mod, db, main_pokemon=None):
    """A bare ItemWindow: no Qt construction, just the collaborators used here."""
    win = mod.ItemWindow.__new__(mod.ItemWindow)
    win.logger = MagicMock()
    win.settings_obj = MagicMock()
    win.main_pokemon = main_pokemon
    win.achievements = {}
    win.renewWidgets = MagicMock()
    win.hp_heal_items = {"potion": 20, "fullrestore": 0}
    win.fossil_pokemon = {}
    win.pokeball_chances = {}
    win._item_action_in_progress = False
    mod.services.db = db
    # Badge bookkeeping is imported by name into the module namespace.
    mod.check_for_badge = MagicMock(return_value=True)
    mod.receive_badge = MagicMock()
    mod.play_effect_sound = MagicMock()
    return win


def test_heal_consumes_exactly_one_item(item_window_mod):
    """The happy path still heals, and spends one potion doing it."""
    db = _FakeItemDB({"potion": 2})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    result = win.Check_Heal_Item("mewtwo", 20, "potion", {})

    assert result is True
    assert pokemon.hp == 30
    assert pokemon.current_hp == 30, "the persisted mirror must follow the heal"
    assert db.quantity_calls == [("potion", -1)]
    assert db.inventory["potion"] == 1


def test_heal_is_refused_when_the_bag_is_empty(item_window_mod):
    """No potion, no heal -- and no write attempt either.

    ``update_item_quantity`` would have logged a warning and returned without
    decrementing anything, which the old code discarded, handing out the HP
    regardless.
    """
    db = _FakeItemDB({})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    result = win.Check_Heal_Item("mewtwo", 20, "potion", {})

    assert result is False
    assert pokemon.hp == 10, "HP was granted for an item the player does not own"
    assert pokemon.current_hp == 10
    assert db.quantity_calls == []
    shown = [
        call.args[1]
        for call in win.logger.log_and_showinfo.call_args_list
        if len(call.args) > 1
    ]
    assert shown, "the player was never told why nothing happened"
    assert any("potion" in msg.lower() for msg in shown), (
        "the refusal must name the item the player tried to use"
    )


def test_heal_is_refused_when_the_row_says_zero(item_window_mod):
    """A leftover row at quantity 0 is not stock."""
    db = _FakeItemDB({"potion": 0})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is False
    assert pokemon.hp == 10
    assert db.quantity_calls == []


def test_second_use_of_the_last_potion_heals_nothing(item_window_mod):
    """The double-use case: one potion cannot pay for two heals."""
    db = _FakeItemDB({"potion": 1})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is True
    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is False
    assert pokemon.hp == 30, "the second click healed for free"
    assert db.quantity_calls == [("potion", -1)]


def test_no_main_pokemon_does_not_burn_an_item(item_window_mod):
    """Nothing to heal means nothing is spent."""
    db = _FakeItemDB({"potion": 1})
    win = _make_window(item_window_mod, db, main_pokemon=None)

    assert win.Check_Heal_Item("nobody", 20, "potion", {}) is False
    assert db.quantity_calls == []
    assert db.inventory["potion"] == 1


def test_badge_is_not_awarded_for_a_refused_heal(item_window_mod):
    """Badge 20 marks having *used* a healing item."""
    db = _FakeItemDB({})
    win = _make_window(item_window_mod, db, _FakePokemon())
    item_window_mod.check_for_badge = MagicMock(return_value=False)
    item_window_mod.receive_badge = MagicMock()

    win.Check_Heal_Item("mewtwo", 20, "potion", {})

    assert not item_window_mod.receive_badge.called


def test_fullrestore_still_heals_to_max_and_clamps(item_window_mod):
    """Existing behaviour is untouched on the successful path."""
    db = _FakeItemDB({"fullrestore": 1})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    assert win.Check_Heal_Item("mewtwo", 0, "fullrestore", {}) is True
    assert pokemon.hp == 100
    assert pokemon.current_hp == 100


def test_dispatch_use_reports_a_refused_heal(item_window_mod):
    """The web Items window must not toast a heal that never happened."""
    db = _FakeItemDB({})
    win = _make_window(item_window_mod, db, _FakePokemon())

    result = win.dispatch_use("potion")

    assert result["ok"] is False
    assert "potion" in result["message"].lower()


def test_dispatch_use_still_reports_a_real_heal(item_window_mod):
    db = _FakeItemDB({"potion": 1})
    win = _make_window(item_window_mod, db, _FakePokemon(hp=10, max_hp=100))

    result = win.dispatch_use("potion")

    assert result["ok"] is True
    assert db.quantity_calls == [("potion", -1)]


def test_delete_item_consumes_one_and_reports_it(item_window_mod):
    db = _FakeItemDB({"potion": 3})
    win = _make_window(item_window_mod, db, _FakePokemon())

    assert win.delete_item("potion") is True
    assert db.inventory["potion"] == 2
    assert win.renewWidgets.called


def test_delete_item_reports_a_missing_item_without_writing(item_window_mod):
    db = _FakeItemDB({})
    win = _make_window(item_window_mod, db, _FakePokemon())

    assert win.delete_item("potion") is False
    assert db.quantity_calls == []
    assert not win.renewWidgets.called
    warnings = [
        call.args[1]
        for call in win.logger.log.call_args_list
        if len(call.args) > 1 and call.args[0] == "warning"
    ]
    assert warnings, "a refused consume left no trace in the log"
    assert "potion" in warnings[0].lower(), "the warning must name the item"


def test_delete_item_survives_a_broken_bag_row(item_window_mod):
    """A malformed quantity is "no usable stock", not a crash mid-click."""
    class _JunkDB(_FakeItemDB):
        def get_item(self, item_name):
            return {"item_name": item_name, "quantity": "lots"}

    db = _JunkDB({"potion": 1})
    win = _make_window(item_window_mod, db, _FakePokemon())

    assert win.delete_item("potion") is False
    assert db.quantity_calls == []


def test_a_failing_bag_redraw_cannot_swallow_the_heal(item_window_mod):
    """The blocker: paying and healing must not be separated by the UI.

    ``delete_item`` used to decrement the bag and *then* rebuild the whole item
    grid before returning, with the heal applied only afterwards. Redrawing
    reads every row and constructs a widget per item, so anything that threw in
    there -- a malformed row, a sprite that will not load, a window whose C++
    side is gone -- left the potion spent and the HP never granted.
    """
    db = _FakeItemDB({"potion": 1})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)
    win.renewWidgets = MagicMock(side_effect=RuntimeError("wrapped C/C++ object deleted"))

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is True
    assert pokemon.hp == 30, "the potion was spent and the heal never landed"
    assert pokemon.current_hp == 30
    assert db.quantity_calls == [("potion", -1)]


def test_the_bag_is_redrawn_only_after_the_heal_lands(item_window_mod):
    """Ordering, not just survival: the grid must never show a half-done use."""
    db = _FakeItemDB({"potion": 1})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)
    hp_at_redraw = []
    win.renewWidgets = lambda: hp_at_redraw.append(pokemon.hp)

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is True
    assert hp_at_redraw == [30], "the bag was redrawn before the HP was granted"


def test_a_row_that_vanishes_after_the_check_heals_nothing(item_window_mod):
    """The non-atomic window: reading the quantity is not paying for the item.

    ``get_item`` and the write that follows it are two separate statements. If
    the row empties in between -- a second click, another window, a background
    writer -- the old code decremented nothing and still returned True, because
    ``update_item_quantity`` reports "the item was not there" with the same 0 it
    uses for "you just spent your last one". The atomic consume answers instead.
    """
    class _VanishingDB(_FakeItemDB):
        def consume_item(self, item_name, count=1):
            self.quantity_calls.append((item_name, -count))
            self.inventory.pop(item_name, None)  # someone else got there first
            return False

    db = _VanishingDB({"potion": 1})
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is False
    assert pokemon.hp == 10, "HP was granted for a potion nobody managed to spend"
    assert pokemon.current_hp == 10


def test_the_heal_button_handler_survives_having_no_main_pokemon(item_window_mod):
    """The bag button's lambda used to dereference ``main_pokemon`` itself.

    ``lambda: self.Check_Heal_Item(self.main_pokemon.name, ...)`` evaluates that
    attribute at click time, so with no main Pokemon it raised AttributeError
    out of a Qt slot and the guard inside ``Check_Heal_Item`` never ran.
    """
    db = _FakeItemDB({"potion": 1})
    win = _make_window(item_window_mod, db, main_pokemon=None)

    assert win._heal_main_pokemon("potion", 20) is False
    assert db.quantity_calls == []
    assert db.inventory["potion"] == 1
    assert win.logger.log_and_showinfo.called, "the click reported nothing to the player"


def test_the_heal_button_handler_heals_the_current_main(item_window_mod):
    """And still routes a normal click through to the heal."""
    db = _FakeItemDB({"potion": 1})
    pokemon = _FakePokemon(name="mewtwo", hp=10, max_hp=100)
    win = _make_window(item_window_mod, db, pokemon)

    assert win._heal_main_pokemon("potion", 20) is True
    assert pokemon.hp == 30
    assert db.quantity_calls == [("potion", -1)]


@pytest.fixture
def real_db(item_window_mod, tmp_path):
    """The real ``AnkimonDB`` on a throwaway file.

    ``_FakeItemDB`` mirrors ``consume_item``'s contract by hand, and a hand
    mirror can drift. This runs the same heal path against real SQLite.
    """
    db_mod = _force_load(
        "Ankimon.pyobj.database_manager",
        _SRC / "Ankimon" / "pyobj" / "database_manager.py",
    )
    return db_mod.AnkimonDB(MagicMock(), db_path=tmp_path / "ankimon.db")


def test_the_heal_path_spends_one_real_row_per_heal(item_window_mod, real_db):
    """End to end over real SQLite: two potions buy two heals, never a third."""
    real_db.save_item(17, "potion", 2)
    pokemon = _FakePokemon(hp=10, max_hp=100)
    win = _make_window(item_window_mod, real_db, pokemon)

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is True
    assert pokemon.hp == 30
    assert real_db.get_item("potion")["quantity"] == 1

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is True
    assert pokemon.hp == 50
    assert real_db.get_item("potion") is None, "the emptied row must not linger"

    assert win.Check_Heal_Item("mewtwo", 20, "potion", {}) is False
    assert pokemon.hp == 50, "the third click healed out of an empty bag"
