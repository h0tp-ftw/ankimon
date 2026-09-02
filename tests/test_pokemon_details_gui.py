"""Qt characterization tests for ``gui_classes/pokemon_details.py`` (row F43).

Pins the OBSERVABLE contract of the ported details panel:

* ``PokemonCollectionDetails`` keeps main's single-``QVBoxLayout`` return shape
  (what the current PC-box details panel consumes), while
  ``PokemonCollectionDetailsSplit`` exposes exp's
  ``(header, stats_tabs, footer, stats_dict)`` components for the PC-box
  overhaul (row F42).
* The ``[No. XXX]`` dex prefix and redundant-nickname suppression.
* The two evolve-button paths (main's singleton-driven button vs exp's
  ``trigger_evo_callback`` button) and the friendship master-toggle guard.
* ``AnimatedStatBar`` clamping and the stat-bar layout's friendship ✓ marker
  plus its DB-less fallback (``services.db`` may be absent headless).
* Move learn/forget and release routed through ``services.db`` — including
  main's XP-share clear guard on release, which exp had dropped.

These need real PyQt6 (a QApplication), so they run in the Qt / Tier-2 env; the
whole module skips cleanly where PyQt6 is absent OR has been mocked in
``sys.modules`` by another (Tier-1) test file -- mirroring
``test_move_picker.py``. Run standalone with::

    pytest tests/test_pokemon_details_gui.py
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.


_MODULE_NAME = "Ankimon.gui_classes.pokemon_details"
_SRC = Path(__file__).parent.parent / "src"

_NOT_READY = {
    "evolvable": False,
    "ready": False,
    "method": None,
    "evo_id": None,
    "evo_name": None,
    "min_happiness": None,
    "current_friendship": 0,
    "friendship_remaining": 0,
    "required_time": None,
    "time_ok": True,
    "status_text": "",
    "bar_max": 400,
    "rejected": False,
}


class _RecorderLogger:
    def __init__(self):
        self.records = []

    def log_and_showinfo(self, level, msg):
        self.records.append((level, msg))

    def log(self, level, msg):
        self.records.append((level, msg))


class _FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.sets = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.sets.append((key, value))
        self.values[key] = value


class _FakeDB:
    """Duck-typed stand-in for the AnkimonDB surface this module touches."""

    def __init__(self, pokemon=None, main_pokemon=None, max_level=50):
        self.pokemon = dict(pokemon or {})
        self.main_pokemon = main_pokemon
        self.max_level = max_level
        self.saved = []
        self.saved_main = []
        self.deleted = []
        self.history = []

    def execute(self, query, parameters=()):
        max_level = self.max_level

        class _Cursor:
            def fetchone(self):
                return (max_level,)

        return _Cursor()

    def get_pokemon(self, individual_id):
        record = self.pokemon.get(individual_id)
        return dict(record) if record else None

    def save_pokemon(self, data):
        self.saved.append(data)
        self.pokemon[data["individual_id"]] = data

    def get_main_pokemon(self):
        return dict(self.main_pokemon) if self.main_pokemon else None

    def save_main_pokemon(self, data):
        self.saved_main.append(data)
        self.main_pokemon = data

    def delete_pokemon(self, individual_id):
        self.deleted.append(individual_id)
        self.pokemon.pop(individual_id, None)
        return True

    def add_to_history(self, data):
        self.history.append(data)
        return True

    def get_all_items(self):
        return []


class _NoIconPath:
    def exists(self):
        return False


@pytest.fixture(autouse=True)
def _env_guard():
    """Skip gracefully when another test file has mocked PyQt6 in this run."""
    from PyQt6.QtWidgets import QDialog

    if not isinstance(QDialog, type):  # PyQt6 was mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_pokemon_details_gui.py standalone"
        )

    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    yield


@pytest.fixture
def details(qapp, tmp_path):
    """Load ``pokemon_details`` in isolation with every module-level dep stubbed."""
    stub_names = (
        "Ankimon",
        "Ankimon.gui_classes",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "Ankimon.services",
        "Ankimon.business",
        "Ankimon.pyobj.attack_dialog",
        "Ankimon.pyobj.pokemon_trade",
        "Ankimon.pyobj.error_handler",
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.pyobj.InfoLogger",
        "Ankimon.pyobj.translator",
        "Ankimon.functions.pokedex_functions",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.friendship_evolution",
        "Ankimon.functions.gui_functions",
        "Ankimon.functions.sprite_functions",
        "Ankimon.gui_entities",
        "Ankimon.utils",
        "Ankimon.resources",
        "Ankimon.texts",
        "aqt",
        _MODULE_NAME,
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QLabel

    for pkg in ("Ankimon", "Ankimon.gui_classes", "Ankimon.functions", "Ankimon.pyobj"):
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod

    aqt_mod = types.ModuleType("aqt")
    aqt_mod.qconnect = lambda signal, func: signal.connect(func)
    aqt_mod.mw = types.SimpleNamespace()
    sys.modules["aqt"] = aqt_mod

    sv = types.ModuleType("Ankimon.services")
    sv.services = types.SimpleNamespace(
        db=None, settings=None, translator=None, ui=None
    )
    sys.modules["Ankimon.services"] = sv

    bz = types.ModuleType("Ankimon.business")
    bz.calculate_pokemon_go_cp = lambda a, d, s, level: 1234
    bz.pokemon_go_raw_stats = lambda stats, iv, ev: (10, 11, 12)
    bz.cp_breakdown_tooltip = lambda payload: "cp tooltip"

    def _split(text, n):
        for i in range(0, len(text), n):
            yield text[i : i + n]

    bz.split_string_by_length = _split
    sys.modules["Ankimon.business"] = bz

    ad = types.ModuleType("Ankimon.pyobj.attack_dialog")

    class AttackDialog:
        # remember_attack() calls AttackDialog(attacks, new_attack, parent=mw)
        # then schedules .raise_()/.activateWindow() for the modal event loop,
        # and .deleteLater() in a finally (the battle-freeze fix plus cleanup).
        def __init__(self, attacks, new_attack, parent=None):
            self.selected_attack = attacks[0]

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def deleteLater(self):
            pass

        def exec(self):
            return 0  # rejected by default

    ad.AttackDialog = AttackDialog
    sys.modules["Ankimon.pyobj.attack_dialog"] = ad

    pt = types.ModuleType("Ankimon.pyobj.pokemon_trade")

    class PokemonTrade:
        def __init__(self, *args, **kwargs):
            pass

    pt.PokemonTrade = PokemonTrade
    sys.modules["Ankimon.pyobj.pokemon_trade"] = pt

    eh = types.ModuleType("Ankimon.pyobj.error_handler")

    def _raise(parent=None, exception=None, message=""):
        raise AssertionError(
            f"show_warning_with_traceback called: {message}"
        ) from exception

    eh.show_warning_with_traceback = _raise
    sys.modules["Ankimon.pyobj.error_handler"] = eh

    po = types.ModuleType("Ankimon.pyobj.pokemon_obj")

    class PokemonObject:
        @staticmethod
        def calc_stat(key, base, level, iv, ev, nature):
            return base + level  # deterministic, checkable

    po.PokemonObject = PokemonObject
    sys.modules["Ankimon.pyobj.pokemon_obj"] = po

    il = types.ModuleType("Ankimon.pyobj.InfoLogger")
    il.ShowInfoLogger = _RecorderLogger
    sys.modules["Ankimon.pyobj.InfoLogger"] = il

    tr = types.ModuleType("Ankimon.pyobj.translator")

    class Translator:
        def __init__(self, language):
            self.language = language

        def translate(self, key, **kwargs):
            if key == "evolve_now_button":
                return f"Evolve into {kwargs.get('evo_name')}!"
            return key

    tr.Translator = Translator
    sys.modules["Ankimon.pyobj.translator"] = tr

    pf = types.ModuleType("Ankimon.functions.pokedex_functions")
    pf.get_pokemon_diff_lang_name = lambda pid, lang: "Pikachu"
    pf.get_pokemon_descriptions = lambda pid, lang: "A mouse Pokemon."
    pf.get_all_pokemon_moves = lambda name, level: ["tackle", "thunderbolt"]
    pf.get_pretty_name_for_name = lambda name: str(name).replace("-", " ").title()
    pf.find_details_move = lambda name: {
        "type": "Normal",
        "category": "Physical",
        "basePower": 40,
        "accuracy": 100,
        "pp": 35,
        "shortDesc": "desc",
    }
    pf.search_pokedex = lambda name, variable: 25 if variable == "species_id" else None
    pf.search_pokedex_by_id = lambda pid: "pikachu"
    sys.modules["Ankimon.functions.pokedex_functions"] = pf

    pkf = types.ModuleType("Ankimon.functions.pokemon_functions")
    pkf.find_experience_for_level = lambda growth_rate, level, capped: 1000
    sys.modules["Ankimon.functions.pokemon_functions"] = pkf

    fe = types.ModuleType("Ankimon.functions.friendship_evolution")
    fe.READINESS = dict(_NOT_READY)
    fe.evolution_readiness = lambda pokemon, now=None: dict(fe.READINESS)
    sys.modules["Ankimon.functions.friendship_evolution"] = fe

    gf = types.ModuleType("Ankimon.functions.gui_functions")
    gf.type_icon_path = lambda t: _NoIconPath()
    gf.move_category_path = lambda c: _NoIconPath()
    sys.modules["Ankimon.functions.gui_functions"] = gf

    sf = types.ModuleType("Ankimon.functions.sprite_functions")
    sf.get_sprite_path = lambda side, fmt, pid, shiny, gender, name=None: (
        tmp_path / "missing.png"
    )
    sys.modules["Ankimon.functions.sprite_functions"] = sf

    ge = types.ModuleType("Ankimon.gui_entities")
    ge.MovieSplashLabel = QLabel
    sys.modules["Ankimon.gui_entities"] = ge

    ut = types.ModuleType("Ankimon.utils")
    ut.format_move_name = lambda s: str(s).replace("-", " ").title()
    ut.load_custom_font = lambda size, language: QFont()
    sys.modules["Ankimon.utils"] = ut

    rs = types.ModuleType("Ankimon.resources")
    rs.icon_path = tmp_path / "icon.png"
    rs.addon_dir = tmp_path
    rs.pokemon_tm_learnset_path = tmp_path / "tm_learnset.json"
    sys.modules["Ankimon.resources"] = rs

    tx = types.ModuleType("Ankimon.texts")
    tx.attack_details_window_template = "<table>"
    tx.attack_details_window_template_end = "</table>"
    tx.remember_attack_details_window_template = "<table>"
    tx.remember_attack_details_window_template_end = "</table>"
    sys.modules["Ankimon.texts"] = tx

    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, _SRC / "Ankimon" / "gui_classes" / "pokemon_details.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)

    # Handy handles for the tests
    module._test_services = sv.services
    module._test_readiness = fe.READINESS

    try:
        yield module
    finally:
        for name, val in saved.items():
            if val is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = val


def _details_kwargs(**overrides):
    """The exact keyword set main's pc_box.show_pokemon_details passes today."""
    kwargs = dict(
        name="pikachu",
        level=12,
        id=25,
        shiny=False,
        ability="static",
        type=["Electric"],
        detail_stats={
            "hp": 35,
            "atk": 55,
            "def": 40,
            "spa": 50,
            "spd": 50,
            "spe": 90,
            "xp": 120,
        },
        attacks=["tackle", "thunder-shock"],
        base_experience=112,
        growth_rate="medium",
        ev={"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        iv={"hp": 31, "atk": 20, "def": 10, "spa": 15, "spd": 25, "spe": 31},
        gender="M",
        nickname=None,
        individual_id="uuid-1",
        pokemon_defeated=3,
        everstone=False,
        evolution_rejected=False,
        captured_date="2024-01-01 10:00:00",
        language=9,
        gif_in_collection=False,
        remove_levelcap=False,
        logger=_RecorderLogger(),
        refresh_callback=lambda: None,
        initial_tab_index=0,
        tab_changed_callback=None,
        nature="jolly",
        base_stats={"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        friendship=70,
        friendship_time_enabled=True,
    )
    kwargs.update(overrides)
    return kwargs


def _labels(widget):
    from PyQt6.QtWidgets import QLabel

    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def _buttons(widget):
    from PyQt6.QtWidgets import QPushButton

    return widget.findChildren(QPushButton)


# ---------------------------------------------------------------------------
# Panel construction contracts
# ---------------------------------------------------------------------------


def test_wrapper_keeps_single_layout_contract_for_base_pc_box(details):
    from PyQt6.QtWidgets import QTabWidget, QVBoxLayout

    layout = details.PokemonCollectionDetails(**_details_kwargs())
    assert isinstance(layout, QVBoxLayout)
    assert layout.count() == 3  # header / stats tabs / footer
    tabs = layout.itemAt(1).widget()
    assert isinstance(tabs, QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Stats", "IV", "EV"]


def test_split_returns_components_and_freshly_computed_stats(details):
    from PyQt6.QtWidgets import QTabWidget, QWidget

    header, tabs, footer, stats = details.PokemonCollectionDetailsSplit(
        **_details_kwargs()
    )
    assert isinstance(header, QWidget)
    assert isinstance(tabs, QTabWidget)
    assert isinstance(footer, QWidget)
    # calc_stat stub returns base+level; xp/friendship pass through.
    assert stats == {
        "hp": 47,
        "atk": 67,
        "def": 52,
        "spa": 62,
        "spd": 62,
        "spe": 102,
        "xp": 120,
        "friendship": 70,
    }


def test_stats_dict_tolerates_missing_iv_ev_keys(details):
    # Persisted records may predate full IV/EV dicts: missing keys default to 0
    # instead of raising KeyError (which the error-handler stub would surface
    # as an AssertionError).
    _, _, _, stats = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(
            iv={"hp": 31, "atk": 20},  # def/spa/spd/spe missing
            ev={},  # fully absent
        )
    )
    # calc_stat stub returns base+level, so every core stat is still computed.
    assert stats["hp"] == 47 and stats["spe"] == 102


def test_header_shows_dex_prefix_and_suppresses_redundant_nickname(details):
    header, _, _, _ = details.PokemonCollectionDetailsSplit(**_details_kwargs())
    texts = _labels(header)
    assert any(t.startswith("[No. 025] Pikachu") for t in texts)

    # A nickname equal to the species name is redundant -> no "(Pikachu)".
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(nickname="Pikachu")
    )
    name_text = next(t for t in _labels(header) if t.startswith("[No. 025]"))
    assert "(" not in name_text

    # A real nickname shows "Nick (Species)".
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(nickname="Sparky")
    )
    name_text = next(t for t in _labels(header) if t.startswith("[No. 025]"))
    assert "Sparky" in name_text and "(Pikachu)" in name_text


def test_nature_row_is_displayed(details):
    header, _, _, _ = details.PokemonCollectionDetailsSplit(**_details_kwargs())
    assert any("Nature: Jolly" in t for t in _labels(header))


# ---------------------------------------------------------------------------
# Evolution UI (both paths + master-toggle guard)
# ---------------------------------------------------------------------------


def _make_ready(details, method="friendship"):
    details._test_readiness.update(
        {
            "evolvable": True,
            "ready": True,
            "method": method,
            "evo_id": 26,
            "evo_name": "Raichu",
            "status_text": "",
        }
    )


def test_base_path_shows_singleton_evolve_button_when_ready(details):
    _make_ready(details, method="friendship")
    header, _, _, _ = details.PokemonCollectionDetailsSplit(**_details_kwargs())
    assert any("Evolve into Raichu now" in b.text() for b in _buttons(header))


def test_friendship_master_toggle_hides_evolution_ui(details):
    _make_ready(details, method="friendship")
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(friendship_time_enabled=False)
    )
    assert not any("Evolve into" in b.text() for b in _buttons(header))

    # A level evolution is base-game behaviour: it ignores the toggle.
    _make_ready(details, method="level")
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(friendship_time_enabled=False)
    )
    assert any("Evolve into Raichu now" in b.text() for b in _buttons(header))


def test_status_text_requirement_line_is_shown_when_not_ready(details):
    details._test_readiness.update(
        {
            "ready": False,
            "method": "friendship",
            "status_text": "40 friendship to evolve into Raichu",
        }
    )
    header, _, _, _ = details.PokemonCollectionDetailsSplit(**_details_kwargs())
    assert any("40 friendship to evolve into Raichu" in t for t in _labels(header))


@pytest.mark.parametrize(
    ("method", "status_text"),
    [
        ("friendship", "40 friendship to evolve into Raichu"),
        ("level", "Evolves into Raichu at Lv20"),
    ],
)
def test_callback_path_keeps_requirement_text_when_not_ready(
    details, method, status_text
):
    """A caller-owned evolve button must not suppress unmet requirements."""
    details._test_readiness.update(
        {
            "evolvable": True,
            "ready": False,
            "method": method,
            "evo_id": 26,
            "evo_name": "Raichu",
            "status_text": status_text,
        }
    )
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(trigger_evo_callback=lambda method: None)
    )

    assert status_text in _labels(header)
    assert not any(
        b.objectName() == "evolveNowButton" for b in _buttons(header)
    )


def test_callback_path_renders_button_and_invokes_callback(details):
    _make_ready(details, method="level")
    calls = []
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(trigger_evo_callback=calls.append)
    )
    button = next(b for b in _buttons(header) if b.objectName() == "evolveNowButton")
    assert "RAICHU" in button.text()  # translator text upper-cased
    # The base-path pink button must NOT also be present.
    assert not any("Evolve into Raichu now" in b.text() for b in _buttons(header))
    button.click()
    assert calls == ["level"]


def test_callback_path_falls_back_when_evo_name_missing(details):
    # A missing evo_name must not reach the translator as None — it falls back
    # to "the next form", matching the base (singleton) path.
    _make_ready(details, method="level")
    details._test_readiness["evo_name"] = None
    header, _, _, _ = details.PokemonCollectionDetailsSplit(
        **_details_kwargs(trigger_evo_callback=lambda method: None)
    )
    button = next(b for b in _buttons(header) if b.objectName() == "evolveNowButton")
    assert "THE NEXT FORM" in button.text()


# ---------------------------------------------------------------------------
# Stat bars
# ---------------------------------------------------------------------------


def test_animated_stat_bar_clamps_to_bar_width(details):
    bar = details.AnimatedStatBar(None, 500.0, 300.0)
    assert bar.current_value == 200.0
    assert bar.new_value == 200.0

    bar = details.AnimatedStatBar(None, 0.0, 150.0)
    assert bar.new_value == 150.0


def _stat_row_labels(layout):
    """Collect all QLabel texts from a PokemonDetailsStats layout."""
    from PyQt6.QtWidgets import QLabel

    texts = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        inner = item.layout()
        if inner is None:
            continue
        for j in range(inner.count()):
            w = inner.itemAt(j).widget()
            if isinstance(w, QLabel):
                texts.append(w.text())
    return texts


def test_stats_layout_marks_met_friendship_and_survives_missing_db(details):
    # services.db is None here: the MAX(level) query must fall back silently.
    assert details._test_services.db is None
    layout = details.PokemonDetailsStats(
        {"hp": 100, "friendship": 450, "xp": 120},
        "medium",
        10,
        False,
        9,
        friendship_bar_max=220,
    )
    texts = _stat_row_labels(layout)
    assert "450 ✓" in texts  # met-threshold marker (base guard kept)

    layout = details.PokemonDetailsStats(
        {"hp": 100, "friendship": 10, "xp": 120},
        "medium",
        10,
        False,
        9,
        friendship_bar_max=220,
    )
    texts = _stat_row_labels(layout)
    assert "10" in texts and not any("✓" in t for t in texts)


def test_stats_layout_uses_db_max_level_when_available(details):
    details._test_services.db = _FakeDB(max_level=80)
    layout = details.PokemonDetailsStats(
        {"hp": 100, "xp": 120},
        "medium",
        10,
        False,
        9,
        old_stats={"hp": 50},
    )
    # One row per known stat (hp, xp), each with two labels (name + value).
    assert len(_stat_row_labels(layout)) == 4


def test_stats_layout_survives_zero_experience_for_level(details):
    # find_experience_for_level returns 0 for unknown growth rates / missing
    # CSV rows; the XP bar mapping (both the old_stats slide source and the
    # new value) must clamp instead of dividing by zero.
    details.find_experience_for_level = lambda growth_rate, level, capped: 0
    layout = details.PokemonDetailsStats(
        {"hp": 100, "xp": 120},
        "unknown-growth-rate",
        1,
        False,
        9,
        old_stats={"xp": 60, "hp": 50},
    )
    # Both rows (hp, xp) built without a ZeroDivisionError.
    assert len(_stat_row_labels(layout)) == 4


# ---------------------------------------------------------------------------
# services.db routing for move + release actions
# ---------------------------------------------------------------------------


def test_remember_attack_saves_via_services_db_and_refreshes(details):
    db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "name": "pikachu",
                "attacks": ["tackle"],
            }
        },
        main_pokemon={"individual_id": "uuid-1", "attacks": ["tackle"]},
    )
    details._test_services.db = db
    refreshed = []
    logger = _RecorderLogger()

    details.remember_attack(
        "uuid-1", ["tackle"], "thunderbolt", logger, lambda: refreshed.append(True)
    )

    assert db.saved and db.saved[-1]["attacks"] == ["tackle", "thunderbolt"]
    # The main pokemon mirror is kept in sync too.
    assert db.saved_main and db.saved_main[-1]["attacks"] == ["tackle", "thunderbolt"]
    assert refreshed == [True]


def test_remember_attack_with_a_full_moveset_goes_through_the_replace_dialog(details):
    # 4 known moves -> the "learn a 5th" path can't just append, it has to
    # prompt AttackDialog(attacks, new_attack, parent=mw) for a replacement —
    # the double's exec() returns 0 (rejected) by default, so the new move is
    # discarded and the original 4 attacks are unchanged.
    db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "name": "pikachu",
                "attacks": ["tackle", "thunderbolt", "quick-attack", "growl"],
            }
        },
        main_pokemon={
            "individual_id": "uuid-1",
            "attacks": ["tackle", "thunderbolt", "quick-attack", "growl"],
        },
    )
    details._test_services.db = db
    logger = _RecorderLogger()

    details.remember_attack(
        "uuid-1",
        ["tackle", "thunderbolt", "quick-attack", "growl"],
        "thunder",
        logger,
        lambda: None,
    )

    # The dialog was declined (double's default), so the moveset is untouched
    # rather than silently growing past 4 or swapping something unintended.
    assert db.saved[-1]["attacks"] == ["tackle", "thunderbolt", "quick-attack", "growl"]


def test_remember_attack_establishes_modality_before_showing_dialog(
    details, monkeypatch
):
    """The fifth-move prompt must be modal before it becomes visible."""
    events = []
    callbacks = []

    class ProbeTimer:
        @staticmethod
        def singleShot(interval, callback):
            assert interval == 0
            events.append("scheduled")
            callbacks.append(callback)

    class ModalProbeAttackDialog:
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

    monkeypatch.setattr(details, "AttackDialog", ModalProbeAttackDialog)
    monkeypatch.setattr(details, "QTimer", ProbeTimer, raising=False)
    details._test_services.db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "name": "pikachu",
                "attacks": ["tackle", "thunderbolt", "quick-attack", "growl"],
            }
        },
        main_pokemon={
            "individual_id": "uuid-1",
            "attacks": ["tackle", "thunderbolt", "quick-attack", "growl"],
        },
    )

    details.remember_attack(
        "uuid-1",
        ["tackle", "thunderbolt", "quick-attack", "growl"],
        "thunder",
        _RecorderLogger(),
        lambda: None,
    )

    assert events == ["scheduled", "exec", "raise", "activate", "delete"]


def test_forget_attack_refuses_to_remove_last_move(details):
    db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "name": "pikachu",
                "attacks": ["tackle"],
            }
        }
    )
    details._test_services.db = db
    logger = _RecorderLogger()

    details.forget_attack("uuid-1", ["tackle"], "tackle", logger)

    assert db.saved[-1]["attacks"] == ["tackle"]
    assert any("can't forget" in msg for _, msg in logger.records)


def _accept_release(details):
    from PyQt6.QtWidgets import QMessageBox

    class _YesBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    details.QMessageBox = _YesBox


def test_pokemon_free_clears_xp_share_before_delete(details):
    _accept_release(details)
    db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "id": 25,
                "name": "pikachu",
            }
        },
        main_pokemon=None,
    )
    settings = _FakeSettings({"trainer.xp_share": "uuid-1"})
    details._test_services.db = db
    details._test_services.settings = settings

    details.PokemonFree("uuid-1", "pikachu", _RecorderLogger(), lambda: None)

    # Base guard (dropped by exp, reinstated here): the stale XP-share target is
    # cleared BEFORE the Pokémon vanishes from the DB.
    assert ("trainer.xp_share", None) in settings.sets
    assert db.deleted == ["uuid-1"]
    assert db.history and db.history[0]["individual_id"] == "uuid-1"


def test_pokemon_free_keeps_unrelated_xp_share(details):
    _accept_release(details)
    db = _FakeDB(
        pokemon={
            "uuid-1": {
                "individual_id": "uuid-1",
                "id": 25,
                "name": "pikachu",
            }
        },
        main_pokemon=None,
    )
    settings = _FakeSettings({"trainer.xp_share": "other-uuid"})
    details._test_services.db = db
    details._test_services.settings = settings

    details.PokemonFree("uuid-1", "pikachu", _RecorderLogger(), lambda: None)

    assert settings.sets == []
    assert db.deleted == ["uuid-1"]
