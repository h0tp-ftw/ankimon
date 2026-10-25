"""Qt characterization tests for ``pyobj/pc_box.py`` (inventory row F42).

The PC-box overhaul replaces the old inline "Evolve now" button in the details
panel with a ``MoveManagerWidget`` (forget/learn/TM). These pin that contract on
main's service seam:

* ``PokemonPC._integrate_move_manager`` mounts a ``MoveManagerWidget`` into the
  details header and relocates the ``evolveNowButton`` to the end of its row --
  exp's "MoveManagerWidget replaces the evolution button" behaviour.
* ``MoveManagerWidget`` builds four move slots plus a "Learn from TMs" button and
  reads its Pokémon row through the ``services.db`` seam (main's architecture),
  not exp's direct ``mw.ankimon_db``.

``pc_box.py`` is loaded in isolation with every module-level dependency stubbed
(``aqt.qt`` re-exports the *real* PyQt6 widgets, so no QtWebEngine import fires),
mirroring ``test_pokemon_details_gui.py`` / ``test_move_picker.py``. Needs real
PyQt6; the module skips cleanly in the aqt-free Tier-1 env or where another test
has mocked PyQt6. Run standalone with::

    pytest tests/test_pc_box_evolution_button.py
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.


_MODULE_NAME = "Ankimon.pyobj.pc_box"
_SRC = Path(__file__).parent.parent / "src"


@pytest.fixture(autouse=True)
def _env_guard():
    """Skip gracefully when another test file has mocked PyQt6 in this run.

    Un-mocking a compiled Qt extension mid-process is not reliable, so we skip
    rather than fail when real Qt is not active. Matches ``test_move_picker.py``.
    """
    from PyQt6.QtWidgets import QDialog

    if not isinstance(QDialog, type):  # PyQt6 was mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_pc_box_evolution_button.py standalone"
        )
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    yield


def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def pc_box(qapp):
    """Force-load ``pc_box.py`` with every module-level dependency stubbed.

    ``aqt.qt`` is stubbed to re-export the *real* PyQt6 classes ``pc_box`` needs,
    so real widgets are built for pytest-qt's ``qapp`` while the crashy
    heavyweight import graph (aqt's QtWebEngine, the addon's sibling windows) is
    never touched.
    """
    from PyQt6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QVBoxLayout,
        QLabel,
        QPushButton,
        QGridLayout,
    )
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import Qt

    # --- aqt namespace -----------------------------------------------------
    fake_hooks = mock.MagicMock()
    aqt_mod = _make_module("aqt", mw=mock.MagicMock(), gui_hooks=fake_hooks)
    aqt_qt_mod = _make_module(
        "aqt.qt",
        Qt=Qt,
        QDialog=QDialog,
        QHBoxLayout=QHBoxLayout,
        QVBoxLayout=QVBoxLayout,
        QLabel=QLabel,
        QPushButton=QPushButton,
        QGridLayout=QGridLayout,
        QPixmap=QPixmap,
    )
    aqt_theme_mod = _make_module(
        "aqt.theme", theme_manager=types.SimpleNamespace(night_mode=False)
    )

    # --- Ankimon service seam + sibling stubs ------------------------------
    services_obj = types.SimpleNamespace(db=None, achievements={})
    services_mod = _make_module("Ankimon.services", services=services_obj)

    class _Stub:
        def __init__(self, *a, **k):
            pass

        @classmethod
        def from_dict(cls, *a, **k):
            return cls()

        @staticmethod
        def calc_stat(*a, **k):
            return 1

    stub_specs = {
        "Ankimon.pyobj.pokemon_obj": {"PokemonObject": _Stub},
        "Ankimon.pyobj.reviewer_obj": {"Reviewer_Manager": _Stub},
        "Ankimon.pyobj.test_window": {"TestWindow": _Stub},
        "Ankimon.pyobj.translator": {"Translator": _Stub},
        "Ankimon.pyobj.collection_dialog": {"MainPokemon": _Stub},
        "Ankimon.gui_classes.pokemon_details": {
            "PokemonCollectionDetailsSplit": lambda *a, **k: (None, None, None, {}),
            "remember_attack": mock.MagicMock(),
        },
        "Ankimon.pyobj.InfoLogger": {"ShowInfoLogger": _Stub},
        "Ankimon.pyobj.move_picker": {"MovePickerDialog": _Stub},
        "Ankimon.pyobj.evolution_window": {"EvoWindow": _Stub},
        "Ankimon.pyobj.settings": {"Settings": _Stub},
        "Ankimon.functions.friendship_evolution": {
            "current_time_label": lambda *a, **k: "Day",
            "evolution_readiness": lambda *a, **k: {"ready": False, "method": None},
        },
        "Ankimon.functions.sprite_functions": {"get_sprite_path": lambda *a, **k: ""},
        "Ankimon.utils": {
            "load_custom_font": lambda *a, **k: mock.MagicMock(),
            "get_tier_by_id": lambda *a, **k: "Normal",
            "is_alive": lambda obj: obj is not None,
            "format_move_name": lambda s: str(s).replace("-", " ").title(),
            "format_pokemon_name": lambda s: str(s).title(),
        },
        "Ankimon.resources": {
            "icon_path": Path("/nonexistent/icon.png"),
            "items_path": Path("/nonexistent/items"),
            "csv_file_items_cost": Path("/nonexistent/items_cost.csv"),
            "poke_evo_path": Path("/nonexistent/evo"),
            "pokemon_tm_learnset_path": Path("/nonexistent/tm.json"),
            "addon_dir": Path("/nonexistent/addon"),
        },
        "Ankimon.business": {"calculate_cp_from_dict": lambda *a, **k: 0},
        "Ankimon.functions.pokedex_functions": {
            "find_details_move": lambda m: {"type": "Normal"},
            "get_all_pokemon_moves": lambda *a, **k: [],
            "format_lore_name": lambda s: s,
            "get_pretty_name_for_name": lambda s: str(s).title(),
            "search_pokedex_by_id": lambda i: "pikachu",
            "search_pokedex": lambda *a, **k: None,
            "items_cost_index_for": lambda *a, **k: {},
            "normalize_item_identifier": lambda value: str(value).lower(),
        },
        "Ankimon.functions.gui_functions": {
            "type_icon_path": lambda *a, **k: Path("/nonexistent"),
            "move_category_path": lambda *a, **k: Path("/nonexistent"),
        },
    }

    # Parent packages so relative imports resolve.
    parent_pkgs = (
        "Ankimon",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "Ankimon.gui_classes",
    )
    to_install = {
        "aqt": aqt_mod,
        "aqt.qt": aqt_qt_mod,
        "aqt.theme": aqt_theme_mod,
        "Ankimon.services": services_mod,
        _MODULE_NAME: None,
        **{name: _make_module(name, **attrs) for name, attrs in stub_specs.items()},
    }
    saved = {name: sys.modules.get(name) for name in (*to_install, *parent_pkgs)}

    for pkg in parent_pkgs:
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod
    for name, mod in to_install.items():
        if mod is not None:
            sys.modules[name] = mod

    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, _SRC / "Ankimon" / "pyobj" / "pc_box.py"
    )
    module = importlib.util.module_from_spec(spec)
    module._services_obj = services_obj  # expose for tests
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)

    try:
        yield module
    finally:
        for name, val in saved.items():
            if val is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = val


def test_integrate_move_manager_replaces_evolution_button(pc_box, monkeypatch):
    """The move manager is mounted and the evolve button is pushed to the end."""
    # Mock the widget itself so we test the layout wiring, not Qt construction.
    fake_move_manager = mock.MagicMock(name="MoveManagerWidget-instance")
    monkeypatch.setattr(
        pc_box, "MoveManagerWidget", mock.MagicMock(return_value=fake_move_manager)
    )

    pc = mock.MagicMock()
    pc.details_widget = None

    # header_stack -> h_widget -> h_layout -> first_layout -> top_r_widget -> top_r_layout
    h_widget = mock.MagicMock()
    pc.header_stack.currentWidget.return_value = h_widget
    h_layout = mock.MagicMock()
    h_widget.layout.return_value = h_layout
    h_layout.count.return_value = 2
    first_layout_item = mock.MagicMock()
    first_layout = mock.MagicMock()
    first_layout_item.layout.return_value = first_layout
    h_layout.itemAt.return_value = first_layout_item
    top_r_item = mock.MagicMock()
    top_r_widget = mock.MagicMock()
    top_r_item.widget.return_value = top_r_widget
    first_layout.itemAt.return_value = top_r_item
    top_r_layout = mock.MagicMock()
    top_r_widget.layout.return_value = top_r_layout

    # The row holds an "otherButton" and the "evolveNowButton".
    evo_btn_widget = mock.MagicMock()
    evo_btn_widget.objectName.return_value = "evolveNowButton"
    layout_item_evo = mock.MagicMock()
    layout_item_evo.widget.return_value = evo_btn_widget
    other_widget = mock.MagicMock()
    other_widget.objectName.return_value = "otherButton"
    layout_item_other = mock.MagicMock()
    layout_item_other.widget.return_value = other_widget
    items = [layout_item_other, layout_item_evo]
    top_r_layout.count.return_value = len(items)
    top_r_layout.itemAt.side_effect = lambda idx: items[idx]

    pokemon = {"individual_id": "test-uuid-123", "id": 790, "name": "Cosmoem"}

    pc_box.PokemonPC._integrate_move_manager(pc, pokemon)

    # A MoveManagerWidget was built for this Pokémon and mounted in the row.
    pc_box.MoveManagerWidget.assert_called_once()
    _, kwargs = pc_box.MoveManagerWidget.call_args
    assert kwargs["individual_id"] == "test-uuid-123"
    assert kwargs["pkmn_id"] == 790
    assert fake_move_manager in [
        c.args[0] for c in top_r_layout.addWidget.call_args_list
    ]

    # The evolve button is removed and re-appended so it stays below the manager.
    top_r_layout.removeWidget.assert_called_once_with(evo_btn_widget)
    add_calls = top_r_layout.addWidget.call_args_list
    assert len(add_calls) >= 2
    assert add_calls[-1].args[0] == evo_btn_widget


def _build_integrate_mocks():
    """A PokemonPC-shaped mock whose header layout resolves to a row we can watch."""
    pc = mock.MagicMock()
    pc.details_widget = None
    h_widget = mock.MagicMock()
    pc.header_stack.currentWidget.return_value = h_widget
    h_layout = mock.MagicMock()
    h_widget.layout.return_value = h_layout
    h_layout.count.return_value = 2
    first_layout_item = mock.MagicMock()
    first_layout = mock.MagicMock()
    first_layout_item.layout.return_value = first_layout
    h_layout.itemAt.return_value = first_layout_item
    top_r_item = mock.MagicMock()
    top_r_widget = mock.MagicMock()
    top_r_item.widget.return_value = top_r_widget
    first_layout.itemAt.return_value = top_r_item
    top_r_layout = mock.MagicMock()
    top_r_widget.layout.return_value = top_r_layout
    top_r_layout.count.return_value = 0
    top_r_layout.itemAt.side_effect = lambda idx: None
    return pc


def test_integrate_move_manager_retires_previous_widget(pc_box, monkeypatch):
    """A stale MoveManagerWidget is deleteLater'd before a new one is built.

    The manager is parented to the persistent ``details_widget``, not the swapped
    header stack, so without an explicit retire it would accumulate as an orphaned
    child on every ``show_pokemon_details`` call (memory/widget leak).
    """
    fake_move_manager = mock.MagicMock(name="MoveManagerWidget-new")
    monkeypatch.setattr(
        pc_box, "MoveManagerWidget", mock.MagicMock(return_value=fake_move_manager)
    )

    pc = _build_integrate_mocks()
    stale = mock.MagicMock(name="MoveManagerWidget-old")  # is_alive(stale) is truthy
    pc.move_manager = stale

    pokemon = {"individual_id": "uuid-A", "id": 25, "name": "Pikachu"}
    pc_box.PokemonPC._integrate_move_manager(pc, pokemon)

    # The previous manager was retired exactly once and replaced by the new one.
    stale.deleteLater.assert_called_once_with()
    assert pc.move_manager is fake_move_manager


def test_move_manager_reads_moves_via_services_db(pc_box):
    """MoveManagerWidget builds 4 slots + TM button, sourcing data from services.db."""
    stored = {
        "name": "pikachu",
        "nickname": "",
        "level": 20,
        "attacks": ["tackle", "growl"],
        "all_attacks": ["tackle", "growl", "thunderbolt"],
    }

    fake_cursor = mock.MagicMock()
    fake_cursor.fetchone.return_value = [json.dumps(stored)]
    fake_db = mock.MagicMock()
    fake_db.execute.return_value = fake_cursor
    pc_box._services_obj.db = fake_db

    widget = pc_box.MoveManagerWidget(
        individual_id="ind-1",
        pkmn_id=25,
        logger=mock.MagicMock(),
        save_fn=mock.MagicMock(),
    )

    # Data was read through the seam, not a direct mw handle.
    assert fake_db.execute.called
    # Four fixed move slots and the TM-learning button exist.
    assert len(widget.slots) == 4
    assert widget.tm_btn is not None
    # The first two slots reflect the stored moves; the rest are empty.
    assert widget.slots[0]["move"] == "tackle"
    assert widget.slots[1]["move"] == "growl"
    assert widget.slots[2]["move"] is None
