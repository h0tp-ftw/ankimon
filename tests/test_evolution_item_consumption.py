"""Tests for stone consumption + nickname carry in ``EvoWindow.evolve_pokemon``.

``pyobj/evolution_window.py`` imports Qt at module top, so this module stubs
``aqt`` / ``aqt.qt`` / ``PyQt6`` (with a real ``QWidget`` stand-in so ``EvoWindow``
can subclass it) and every heavy dependency, then loads the real
``evolution_window`` module and drives ``evolve_pokemon`` directly. DB access is
routed through the ``services.db`` seam on main (exp reached ``mw.ankimon_db``),
so the tests inject the mock DB via ``services.db``.

Covers:
* item-triggered evolutions consume one stone (``update_item_quantity(name, -1)``);
* the nickname is rewritten to the pretty evolved name only when it was never
  customised (empty / still matching the pre-evolution species), and a custom
  nickname is preserved.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_src = Path(__file__).parent.parent / "src"


class MockQWidget:
    """Minimal ``QWidget`` stand-in so ``EvoWindow(QWidget)`` can be defined."""

    def __init__(self, *args, **kwargs):
        pass

    def setStyleSheet(self, *args):
        pass

    def setMaximumWidth(self, *args):
        pass

    def setMaximumHeight(self, *args):
        pass

    def layout(self):
        return MagicMock()

    def setLayout(self, *args):
        pass

    def close(self):
        pass

    def show(self):
        pass


def setup_mocks():
    # Packages with __path__ so the module's relative imports resolve.
    for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        if _pkg not in sys.modules or isinstance(sys.modules[_pkg], MagicMock):
            _mod = types.ModuleType(_pkg)
            _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
            _mod.__package__ = _pkg
            sys.modules[_pkg] = _mod

    # External + heavy submodules stubbed (NOT Ankimon.services — the real,
    # aqt-free registry is loaded so services.db can be injected).
    for name in [
        "aqt",
        "aqt.utils",
        "aqt.gui_hooks",
        "aqt.operations",
        "aqt.reviewer",
        "aqt.webview",
        "aqt.main",
        "aqt.theme",
        "PyQt6",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
        "PyQt6.QtCore",
        "anki",
        "anki.hooks",
        "anki.collection",
        "Ankimon.singletons",
        "Ankimon.pyobj.error_handler",
        "Ankimon.pyobj.attack_dialog",
        "Ankimon.pyobj.settings",
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.pyobj.InfoLogger",
        "Ankimon.pyobj.translator",
        "Ankimon.pyobj.test_window",
        "Ankimon.pyobj.reviewer_obj",
        "Ankimon.resources",
        "Ankimon.business",
        "Ankimon.utils",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.battle_functions",
        "Ankimon.functions.update_main_pokemon",
        "Ankimon.functions.badges_functions",
    ]:
        sys.modules[name] = MagicMock()

    # aqt.qt needs a real QWidget/QDialog so EvoWindow(QWidget) is a valid class.
    aqt_qt = MagicMock()
    aqt_qt.QWidget = MockQWidget
    aqt_qt.QDialog = MockQWidget
    sys.modules["aqt.qt"] = aqt_qt


def _force_load(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_evo_window():
    """Load the real evolution_window + a real (mockable) pokedex_functions."""
    setup_mocks()
    pokedex_funcs = _force_load(
        "Ankimon.functions.pokedex_functions",
        _src / "Ankimon" / "functions" / "pokedex_functions.py",
    )
    evo_mod = _force_load(
        "Ankimon.pyobj.evolution_window",
        _src / "Ankimon" / "pyobj" / "evolution_window.py",
    )
    return evo_mod, pokedex_funcs


def _make_evo_window(evo_mod):
    class LocalMockEvoWindow(evo_mod.EvoWindow):
        def __init__(self):
            self.logger = MagicMock()
            self.translator = MagicMock()
            self.reviewer_obj = MagicMock()
            self.test_window = MagicMock()
            self.achievements = {}

    win = LocalMockEvoWindow()
    win.display_evo_complete = MagicMock()
    return win


def _apply_common_patches():
    """Start patches for the pure helpers evolve_pokemon calls; return handles."""
    p = "Ankimon.pyobj.evolution_window."
    handles = {
        "search": patch(p + "search_pokedex").start(),
        "moves": patch(p + "_moves_gained_on_evolution").start(),
        "hp": patch(p + "calculate_hp").start(),
        "growth": patch(p + "get_growth_rate").start(),
        "base_exp": patch(p + "get_base_experience").start(),
        "cp": patch(p + "calculate_cp_from_dict").start(),
        "update_main": patch(p + "update_main_pokemon").start(),
        "badge": patch(p + "check_for_badge").start(),
        "is_alive": patch(p + "is_alive", return_value=False).start(),
    }
    handles["moves"].return_value = []
    handles["hp"].return_value = 100
    handles["growth"].return_value = "medium"
    handles["base_exp"].return_value = 100
    handles["cp"].return_value = 500
    handles["update_main"].return_value = (None, None)
    handles["badge"].return_value = True
    return handles


def _install_dialog_order_probe(evo_mod):
    """Record the move prompt's event-loop ordering without importing Qt."""
    events = []
    callbacks = []

    class ProbeTimer:
        @staticmethod
        def singleShot(interval, callback):
            assert interval == 0
            events.append("scheduled")
            callbacks.append(callback)

    class ProbeDialogCode:
        Accepted = 1

    class ProbeQDialog:
        DialogCode = ProbeDialogCode

    class ProbeAttackDialog:
        def __init__(self, attacks, new_attack, parent=None):
            self.selected_attack = attacks[0]

        def show(self):
            events.append("show")

        def raise_(self):
            events.append("raise")

        def activateWindow(self):
            events.append("activate")

        def exec(self):
            events.append("exec")
            while callbacks:
                callbacks.pop(0)()
            return 0

        def deleteLater(self):
            events.append("delete")

    evo_mod.QTimer = ProbeTimer
    evo_mod.QDialog = ProbeQDialog
    evo_mod.AttackDialog = ProbeAttackDialog
    return events


def test_evolve_pokemon_consumes_stone():
    evo_mod, _ = _load_evo_window()
    mock_db = MagicMock()
    evo_mod.services.db = mock_db  # route the DB seam at the injected mock

    handles = _apply_common_patches()
    try:
        handles["search"].side_effect = lambda name, key: (
            ["Fire"] if key == "types" else {"hp": 50} if key == "baseStats" else {}
        )
        mock_db.get_pokemon.return_value = {
            "id": 133,
            "name": "Eevee",
            "level": 20,
            "attacks": [],
            "iv": {},
            "ev": {},
            "xp": 100,
        }

        win = _make_evo_window(evo_mod)
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=133,
            prevo_name="eevee",
            evo_id=136,
            evo_name="flareon",
            main_pokemon=None,
            item_name="fire-stone",
        )

        mock_db.update_item_quantity.assert_called_once_with("fire-stone", -1)
    finally:
        patch.stopall()


def test_evolve_pokemon_nickname_update():
    evo_mod, pokedex_funcs = _load_evo_window()
    mock_db = MagicMock()
    evo_mod.services.db = mock_db

    handles = _apply_common_patches()
    try:
        handles["search"].side_effect = lambda name, key: (
            ["Psychic"] if key == "types" else {"hp": 40} if key == "baseStats" else {}
        )

        def get_pretty_name_mock(sid):
            if int(sid) == 439:
                return "Mime Jr."
            if int(sid) == 122:
                return "Mr. Mime"
            return "Unknown"

        pokedex_funcs.get_pretty_name_for_id = get_pretty_name_mock

        win = _make_evo_window(evo_mod)

        # Case 1: default nickname (matches pretty/CSV prevo name) -> pretty evo.
        pokemon_data = {
            "id": 439,
            "name": "Mime Jr.",
            "nickname": "Mime Jr.",
            "level": 32,
            "attacks": ["Mimic"],
            "iv": {},
            "ev": {},
            "xp": 100,
        }
        mock_db.get_pokemon.return_value = pokemon_data
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=439,
            prevo_name="mime-jr",
            evo_id=122,
            evo_name="mr-mime",
            main_pokemon=None,
        )
        assert pokemon_data["nickname"] == "Mr. Mime"

        # Case 2: custom nickname preserved.
        pokemon_data_custom = {
            "id": 439,
            "name": "Mime Jr.",
            "nickname": "Sparky",
            "level": 32,
            "attacks": ["Mimic"],
            "iv": {},
            "ev": {},
            "xp": 100,
        }
        mock_db.get_pokemon.return_value = pokemon_data_custom
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=439,
            prevo_name="mime-jr",
            evo_id=122,
            evo_name="mr-mime",
            main_pokemon=None,
        )
        assert pokemon_data_custom["nickname"] == "Sparky"
    finally:
        patch.stopall()


def test_evolve_pokemon_regional_form_growth_rate_fallback():
    """A regional-form target (10xxx id) whose growth rate can't be resolved must
    still evolve, keeping the pre-evolution's growth rate instead of aborting.

    On the integration base ``get_growth_rate`` raises ``ValueError`` for the
    10xxx form ids that this unit surfaces (Cubone -> Alolan Marowak etc.), until
    F17's graceful-fallback version merges. ``evolve_pokemon`` must swallow that
    and complete the evolution (``save_pokemon`` still called), not bubble the
    error to the outer handler and leave the Pokémon unevolved.
    """
    evo_mod, _ = _load_evo_window()
    mock_db = MagicMock()
    evo_mod.services.db = mock_db

    handles = _apply_common_patches()
    try:
        handles["search"].side_effect = lambda name, key: (
            ["Fire", "Ghost"]
            if key == "types"
            else {"hp": 60}
            if key == "baseStats"
            else {}
        )
        # Simulate the base's get_growth_rate raising on the 10xxx form id.
        handles["growth"].side_effect = ValueError(10115)

        pokemon_data = {
            "id": 104,
            "name": "Cubone",
            "nickname": "",
            "level": 28,
            "attacks": [],
            "iv": {},
            "ev": {},
            "xp": 100,
            "growth_rate": "medium",
        }
        mock_db.get_pokemon.return_value = pokemon_data

        win = _make_evo_window(evo_mod)
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=104,
            prevo_name="cubone",
            evo_id=10115,
            evo_name="marowak-alola",
            main_pokemon=None,
        )

        # Evolution completed (not aborted by the raised ValueError) ...
        mock_db.save_pokemon.assert_called_once()
        # ... and the pre-evolution's growth rate was preserved as the fallback.
        assert pokemon_data["growth_rate"] == "medium"
        assert int(pokemon_data["id"]) == 10115
    finally:
        patch.stopall()


def test_evolve_pokemon_establishes_modality_before_foregrounding_move_dialog():
    evo_mod, pokedex_funcs = _load_evo_window()
    mock_db = MagicMock()
    mock_db.save_pokemon.return_value = True
    mock_db.get_pokemon.return_value = {
        "id": 133,
        "name": "Eevee",
        "nickname": "Eevee",
        "level": 20,
        "attacks": ["tackle", "growl", "bite", "swift"],
        "iv": {},
        "ev": {},
        "xp": 100,
    }
    evo_mod.services.db = mock_db
    events = _install_dialog_order_probe(evo_mod)

    handles = _apply_common_patches()
    try:
        handles["moves"].return_value = ["quick-attack"]
        handles["search"].side_effect = lambda name, key: {
            "types": ["Normal"],
            "baseStats": {"hp": 50},
            "abilities": {"0": "run-away"},
        }[key]
        pokedex_funcs.get_pretty_name_for_id = lambda pokemon_id: (
            "Eevee" if int(pokemon_id) == 133 else "Vaporeon"
        )

        win = _make_evo_window(evo_mod)
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=133,
            prevo_name="eevee",
            evo_id=134,
            evo_name="vaporeon",
            main_pokemon=None,
        )

        assert events == ["scheduled", "exec", "raise", "activate", "delete"]
    finally:
        patch.stopall()


def test_cancel_evolution_establishes_modality_before_foregrounding_move_dialog():
    evo_mod, _ = _load_evo_window()
    mock_db = MagicMock()
    mock_db.get_pokemon.return_value = {
        "id": 133,
        "name": "Eevee",
        "level": 20,
        "attacks": ["tackle", "growl", "bite", "swift"],
    }
    evo_mod.services.db = mock_db
    events = _install_dialog_order_probe(evo_mod)

    with patch(
        "Ankimon.pyobj.evolution_window.get_levelup_move_for_pokemon",
        return_value=["quick-attack"],
    ):
        win = _make_evo_window(evo_mod)
        win.main_pokemon = None
        win.cancel_evolution("some-uuid", "eevee")

    assert events == ["scheduled", "exec", "raise", "activate", "delete"]


def test_moves_gained_on_evolution_unions_levelup_and_evolution_moves():
    """Evolving grants this level's level-up moves plus the species' "9L0"
    on-evolution moves, deduped and level-up first."""
    evo_mod, _ = _load_evo_window()

    p = "Ankimon.pyobj.evolution_window."
    with (
        patch(p + "get_levelup_move_for_pokemon", return_value=["tackle", "vinewhip"]),
        patch(
            p + "get_evolution_moves_for_pokemon",
            return_value=["petalblizzard", "tackle"],
        ),
    ):
        assert evo_mod._moves_gained_on_evolution("venusaur", 32) == [
            "tackle",
            "vinewhip",
            "petalblizzard",
        ]


def test_evolve_pokemon_persists_evolution_only_move():
    """A "9L0" move granted on evolution must land in the saved attack list."""
    evo_mod, _ = _load_evo_window()
    mock_db = MagicMock()
    evo_mod.services.db = mock_db

    handles = _apply_common_patches()
    try:
        handles["search"].side_effect = lambda name, key: (
            ["Grass"] if key == "types" else {"hp": 80} if key == "baseStats" else {}
        )
        handles["moves"].return_value = ["petalblizzard"]
        mock_db.get_pokemon.return_value = {
            "id": 2,
            "name": "Ivysaur",
            "level": 32,
            "attacks": ["tackle"],
            "iv": {},
            "ev": {},
            "xp": 100,
        }

        win = _make_evo_window(evo_mod)
        win.evolve_pokemon(
            individual_id="some-uuid",
            prevo_id=2,
            prevo_name="ivysaur",
            evo_id=3,
            evo_name="venusaur",
            main_pokemon=None,
        )

        handles["moves"].assert_called_once_with("venusaur", 32)
        saved = mock_db.save_pokemon.call_args[0][0]
        assert saved["attacks"] == ["tackle", "petalblizzard"]
        assert saved["battle_status"] == "fighting"
    finally:
        patch.stopall()


def test_cancel_evolution_grants_levelup_moves_but_not_evolution_moves():
    """Declining keeps the Pokemon as its pre-evolution, so it may still learn
    this level's ordinary move, but never the evolution-only "9L0" move."""
    evo_mod, _ = _load_evo_window()
    mock_db = MagicMock()
    mock_db.get_pokemon.return_value = {
        "id": 2,
        "name": "Ivysaur",
        "level": 32,
        "attacks": ["tackle"],
    }
    evo_mod.services.db = mock_db

    p = "Ankimon.pyobj.evolution_window."
    with (
        patch(p + "get_levelup_move_for_pokemon", return_value=["sleeppowder"]),
        patch(
            p + "get_evolution_moves_for_pokemon", return_value=["petalblizzard"]
        ) as evo_moves,
    ):
        win = _make_evo_window(evo_mod)
        win.main_pokemon = None
        win.cancel_evolution("some-uuid", "ivysaur")

    evo_moves.assert_not_called()
    saved = mock_db.save_pokemon.call_args[0][0]
    assert saved["attacks"] == ["tackle", "sleeppowder"]
    assert saved["evolution_rejected"] is True
