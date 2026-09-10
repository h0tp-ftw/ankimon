"""Regression tests for stale persisted ``cp`` repair in ``pyobj/pc_box.py``.

``cp`` is derived from ``business.calculate_cpm`` but is *also* persisted per
Pokemon at catch time, so retuning the CPM formula leaves old-scale values in
the database. ``PokemonPC.ensure_data_integrity`` contains an "Always
recalculate CP" pass for exactly that, but it sits behind a quick-check that
returned early whenever every default field was present — which is precisely
the shape of a save carrying pre-retune CP. These pin that the quick-check also
considers CP staleness.

``pc_box.py`` is loaded in isolation with its module-level dependencies stubbed
(mirroring ``test_pc_box_evolution_button.py``), except that the *real*
``Ankimon.business`` is installed because the CP maths is what is under test.
``ensure_data_integrity`` touches no ``self`` state, so it is driven unbound
against a fake ``services.db`` seam — no Qt widget is constructed.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.

_MODULE_NAME = "Ankimon.pyobj.pc_box"
_SRC = Path(__file__).parent.parent / "src"

# Every key the quick-check treats as required. A row holding all of these
# used to short-circuit the migration regardless of how stale its CP was.
_DEFAULT_KEYS = (
    "nickname", "gender", "ability", "type", "attacks", "base_experience",
    "growth_rate", "everstone", "shiny", "captured_date", "individual_id",
    "mega", "special_form", "xp", "friendship", "pokemon_defeated", "tier",
    "is_favorite", "held_item", "cp",
)

# A Pokemon with real base stats so CP is a meaningful number rather than the
# minimum clamp. Mirrors the caught-Pokemon shape ("stats" holds base stats).
_BASE_STATS = {"hp": 106, "atk": 110, "def": 90, "spa": 154, "spd": 90, "spe": 130}


def _complete_row(**overrides):
    """A row that satisfies every default key, so only CP staleness can trigger."""
    row = {key: "placeholder" for key in _DEFAULT_KEYS}
    row.update(
        {
            "id": 150,
            "name": "mewtwo",
            "level": 100,
            "stats": dict(_BASE_STATS),
            "iv": {k: 31 for k in _BASE_STATS},
            "ev": {k: 0 for k in _BASE_STATS},
        }
    )
    row.update(overrides)
    return row


def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def pc_box():
    """Load ``pc_box.py`` with stubbed deps but the real ``business`` module."""
    from PyQt6 import QtCore, QtGui, QtWidgets

    if not isinstance(QtWidgets.QDialog, type):  # PyQt6 mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_pc_box_cp_migration.py standalone"
        )
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

    class _AqtQt(types.ModuleType):
        """``aqt.qt`` re-exporting the real Qt names, mocking anything else.

        Real classes matter because ``PokemonPC`` subclasses ``QDialog``; the
        long tail of aqt-only helpers can safely be mocks.
        """

        def __getattr__(self, name):
            for mod in (QtWidgets, QtGui, QtCore):
                obj = getattr(mod, name, None)
                if obj is not None:
                    return obj
            return mock.MagicMock()

    services_obj = types.SimpleNamespace(db=None, achievements={})

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
            # Required by the real business module at import time.
            "csv_file_items": Path("/nonexistent/items.csv"),
            "csv_file_descriptions": Path("/nonexistent/descriptions.csv"),
            "effectiveness_chart_file_path": (
                _SRC / "Ankimon" / "addon_files" / "eff_chart.json"
            ),
        },
        "Ankimon.functions.pokedex_functions": {
            "find_details_move": lambda m: {"type": "Normal"},
            "get_all_pokemon_moves": lambda *a, **k: [],
            "format_lore_name": lambda s: s,
            "get_pretty_name_for_name": lambda s: str(s).title(),
            "search_pokedex_by_id": lambda i: "pikachu",
        },
        "Ankimon.functions.gui_functions": {
            "type_icon_path": lambda *a, **k: Path("/nonexistent"),
            "move_category_path": lambda *a, **k: Path("/nonexistent"),
        },
    }

    parent_pkgs = ("Ankimon", "Ankimon.functions", "Ankimon.pyobj", "Ankimon.gui_classes")
    to_install = {
        "aqt": _make_module("aqt", mw=mock.MagicMock(), gui_hooks=mock.MagicMock()),
        "aqt.qt": _AqtQt("aqt.qt"),
        "aqt.theme": _make_module(
            "aqt.theme", theme_manager=types.SimpleNamespace(night_mode=False)
        ),
        "Ankimon.services": _make_module("Ankimon.services", services=services_obj),
        _MODULE_NAME: None,
        **{name: _make_module(name, **attrs) for name, attrs in stub_specs.items()},
    }
    saved = {
        name: sys.modules.get(name)
        for name in (*to_install, *parent_pkgs, "Ankimon.business")
    }

    for pkg in parent_pkgs:
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod
    for name, mod in to_install.items():
        if mod is not None:
            sys.modules[name] = mod

    # The real business module — the CP maths is the subject of these tests.
    sys.modules.pop("Ankimon.business", None)
    business_spec = importlib.util.spec_from_file_location(
        "Ankimon.business", _SRC / "Ankimon" / "business.py"
    )
    business = importlib.util.module_from_spec(business_spec)
    sys.modules["Ankimon.business"] = business
    business_spec.loader.exec_module(business)

    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, _SRC / "Ankimon" / "pyobj" / "pc_box.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)

    module._services_obj = services_obj  # expose for tests
    module._business = business
    try:
        yield module
    finally:
        for name, val in saved.items():
            if val is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = val


def _run_integrity(pc_box, rows):
    """Drive ensure_data_integrity unbound against a fake db seam."""
    saved_rows = []
    pc_box._services_obj.db = types.SimpleNamespace(
        get_all_pokemon=lambda: rows,
        save_pokemon=saved_rows.append,
    )
    # ensure_data_integrity touches no self state, so a dummy receiver is fine.
    pc_box.PokemonPC.ensure_data_integrity(object())
    return saved_rows


def test_stale_cp_is_repaired_when_all_default_keys_present(pc_box):
    """The old-scale CP of a fully-populated legacy row gets rewritten.

    Regression: the quick-check used to return early because no default key
    was missing, so the "Always recalculate CP" pass never ran and the
    pre-retune value survived indefinitely.
    """
    expected = pc_box._business.calculate_cp_from_dict(_complete_row())
    row = _complete_row(cp=1460)  # value produced by the superseded 0.84/20 CPM
    assert row["cp"] != expected, "fixture must actually be stale"

    saved = _run_integrity(pc_box, [row])

    assert row["cp"] == expected
    assert saved, "the repaired row must be persisted"


def test_fresh_row_is_not_rewritten(pc_box):
    """A row already matching the formula triggers no write.

    This is the control for the test above: it proves the repair there was
    triggered by CP staleness specifically, not by some missing default key,
    and that the quick-check still avoids pointless database churn.
    """
    row = _complete_row()
    row["cp"] = pc_box._business.calculate_cp_from_dict(row)

    saved = _run_integrity(pc_box, [row])

    assert saved == []


def test_malformed_row_does_not_raise(pc_box):
    """A corrupt legacy row must not take down PC-box open.

    The CP recompute now runs for every row on every open, so it is on the
    critical path of the constructor; ``ensure_data_integrity`` is written to
    repair what it can rather than propagate.
    """
    bad = _complete_row(stats="not-a-dict", iv=None, level="???")

    _run_integrity(pc_box, [bad])  # must not raise
