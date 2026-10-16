"""Qt characterization tests for ``pyobj/test_window.py`` (row F51).

Pins the OBSERVABLE contract of the ported encounter display:

* ``init_ui`` builds ONE persistent layout (a ``main_label`` plus a hidden
  death-screen ``button_widget``) in a margin-free window pinned to the battle
  scenes' 556px width, with the height left to the layout — no
  rebuild-per-encounter: the layout and label objects keep their identity
  across ``display_first_encounter`` / ``display_battle`` /
  ``display_pokemon_death`` calls.
* No view is cropped: every scene the window renders fits inside
  ``main_label`` at its natural size (the regression a fixed 556x300 window
  caused — it cut the message bar off the foot of the battle scene and clipped
  11px off each side of it).
* ``_get_display_name`` routes mega/gmax internal names through the base's
  ``pokedex_functions.get_pretty_name_for_name`` (expectations validated
  against the real ``data_files/pokedex.json``), and everything else through
  the localized ``get_pokemon_diff_lang_name``.
* ``pokemon_display_battle`` no longer increments
  ``tracker.pokemon_encounter`` (the battle loop owns the counter) and
  ``display_first_encounter`` resets it to 0.
* The debounce is keyed per view: an immediate SAME-view repeat is dropped
  (exp's anti-flicker), while the battle->death transition — which main's
  battle loop produces on every faint — always renders.
* The death screen's catch/defeat buttons route through the base
  ``hook_registry`` seam (not ``mw.catchpokemon``/``mw.defeatpokemon``).

These need real PyQt6 (a QApplication), so they run in the Qt / Tier-2 env;
the whole module skips cleanly where PyQt6 is absent — mirroring
``test_pokemon_details_gui.py``. Run standalone with::

    pytest tests/test_test_window_gui.py
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.

_MODULE_NAME = "Ankimon.pyobj.test_window"
_SRC = Path(__file__).parent.parent / "src"

# A real, in-repo PNG so the sprite-scaling math runs on a non-null pixmap.
_REAL_SPRITE = _SRC / "Ankimon" / "ankimon_logo.png"

_EN_LANGUAGE = 9  # Translator LANG_NUMBERS: 9 -> "en"


class _FakeSettings:
    def __init__(self, values=None):
        self.values = {
            "misc.language": _EN_LANGUAGE,
            "misc.remove_level_cap": False,
        }
        self.values.update(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeTracker:
    def __init__(self):
        self.attack_counter = 7
        self.caught = 1
        self.pokemon_encounter = 3
        self.cards_battle_round = 0
        self.battlescene_file = "grass_pkmnbattlescene.png"


class _FakePokemon:
    def __init__(self, name, id, **kw):
        self.name = name
        self.id = id
        self.level = kw.get("level", 5)
        self.hp = kw.get("hp", 12)
        self.max_hp = kw.get("max_hp", 20)
        self.shiny = kw.get("shiny", False)
        self.type = kw.get("type", ["fire"])
        self.gender = kw.get("gender", "M")
        self.stat_stages = kw.get("stat_stages", {})
        self.xp = kw.get("xp", 0)
        self.growth_rate = kw.get("growth_rate", "medium")
        self.cp = kw.get("cp", 345)
        # Defaults to a real PNG so the sprite-scaling math runs; the issue #101
        # tests override it with a path that does not exist, modelling a sprite
        # the user never downloaded.
        self.sprite_path = kw.get("sprite_path", _REAL_SPRITE)

    def get_sprite_path(self, side, ext):
        return self.sprite_path


class _FakeClock:
    """Deterministic stand-in for the module's ``time`` import."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def _env_guard():
    """Skip gracefully when another test file has mocked PyQt6 in this run.

    Some Tier-1 test files install partial ``PyQt6`` stubs in ``sys.modules``
    at import time (e.g. ``test_settings_init_order``), which makes the
    module-level ``importorskip`` succeed while real Qt is absent — so guard
    each test too.
    """
    try:
        from PyQt6.QtWidgets import QDialog
    except ImportError:
        pytest.skip(
            "real PyQt6 not active (stubbed by another test); "
            "run tests/test_test_window_gui.py standalone"
        )

    if not isinstance(QDialog, type):  # PyQt6 was mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_test_window_gui.py standalone"
        )

    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    yield


@pytest.fixture
def tw_module(qapp):
    """Load the real ``test_window`` module with only ``aqt`` stubbed.

    Every Ankimon dependency in its import chain is aqt-free at import time,
    so the module runs its REAL code (real pokedex data, real translator,
    real painters) against a light PyQt6-backed ``aqt`` stub.
    """
    stub_names = (
        "Ankimon",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "aqt",
        "aqt.qt",
        "aqt.utils",
        _MODULE_NAME,
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    for pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod

    import PyQt6.QtCore as _QtCore
    import PyQt6.QtGui as _QtGui
    import PyQt6.QtWidgets as _QtWidgets

    try:
        from PyQt6 import sip as _sip
    except ImportError:  # pragma: no cover - sip packaging differences
        _sip = None

    qt_mod = types.ModuleType("aqt.qt")
    for src in (_QtCore, _QtGui, _QtWidgets):
        for attr in dir(src):
            if not attr.startswith("_"):
                setattr(qt_mod, attr, getattr(src, attr))
    qt_mod.qconnect = lambda signal, func: signal.connect(func)
    if _sip is not None:
        qt_mod.sip = _sip

    utils_mod = types.ModuleType("aqt.utils")
    utils_mod.showWarning = lambda *a, **k: None

    aqt_mod = types.ModuleType("aqt")
    aqt_mod.mw = None
    aqt_mod.qt = qt_mod
    aqt_mod.utils = utils_mod
    aqt_mod.qconnect = qt_mod.qconnect

    sys.modules["aqt"] = aqt_mod
    sys.modules["aqt.qt"] = qt_mod
    sys.modules["aqt.utils"] = utils_mod

    sys.modules.pop(_MODULE_NAME, None)
    module = importlib.import_module(_MODULE_NAME)

    yield module

    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


@pytest.fixture
def make_window(tw_module):
    windows = []

    def _make(main=None, enemy=None, tracker=None):
        main = main or _FakePokemon("pikachu", 25, type=["electric"])
        enemy = enemy or _FakePokemon("charizard", 6, type=["fire", "flying"])
        tracker = tracker or _FakeTracker()
        win = tw_module.TestWindow(
            main_pokemon=main,
            enemy_pokemon=enemy,
            settings_obj=_FakeSettings(),
            parent=None,
            ankimon_tracker_obj=tracker,
        )
        windows.append(win)
        return win

    yield _make

    for win in windows:
        win.deleteLater()


def test_init_ui_builds_persistent_scaffolding(make_window):
    win = make_window()

    layout = win.layout()
    assert layout is not None
    assert layout.count() == 2  # main_label + button_widget, nothing else
    assert layout.itemAt(0).widget() is win.main_label
    assert layout.itemAt(1).widget() is win.button_widget
    assert win.button_widget.isHidden()

    # Width is pinned to the battle scenes' own width; the height is the
    # layout's to decide, so a taller view can never be cut off.
    assert win.minimumWidth() == win.maximumWidth() == 556
    assert win.maximumHeight() > win.minimumHeight()

    # No contents margins: the 556px-wide scene has to reach both edges of a
    # 556px-wide window, and QVBoxLayout's default 11px inset would crop it.
    margins = layout.contentsMargins()
    assert (margins.left(), margins.right()) == (0, 0)

    assert "rgb(44,44,44)" in win.styleSheet()

    # The Ankimon logo landed on the persistent label
    assert win.main_label.pixmap() is not None
    assert not win.main_label.pixmap().isNull()
    assert win.windowTitle() == "Ankimon Window"


@pytest.mark.parametrize("view", ["first_encounter", "battle", "death"])
def test_no_view_is_cropped(make_window, qapp, view):
    """Every view renders at its natural size — nothing is cut off.

    The regression this pins: ``init_ui`` used to pin the window to 556x300,
    but the battle scene WITH its dialog box is 556x371, so the message bar
    along its foot fell outside the window entirely. Both axes were wrong —
    the layout's default 11px side margins also left the 556px-wide scene only
    534px to draw in, shaving 11px off each side of every view.

    Asserting on ``main_label`` rather than on the size constraints is what
    makes this bite: a QLabel silently centre-crops a pixmap too big for it,
    so the only honest question is whether the label is at least as large as
    what it was handed. The window has to be shown for Qt to resolve the
    layout against the real constraints (the suite runs offscreen).
    """
    win = make_window()
    win.show()
    qapp.processEvents()

    if view == "first_encounter":
        win.ankimon_tracker_obj.pokemon_encounter = 0  # the 556x371 scene
        win.display_first_encounter()
    elif view == "battle":
        win.ankimon_tracker_obj.pokemon_encounter = 3
        win.display_battle()
    else:
        win.display_pokemon_death()
    qapp.processEvents()

    pixmap = win.main_label.pixmap()
    assert pixmap is not None and not pixmap.isNull()

    assert win.main_label.width() >= pixmap.width(), (
        f"{view}: {pixmap.width()}px-wide art cropped to a "
        f"{win.main_label.width()}px label"
    )
    assert win.main_label.height() >= pixmap.height(), (
        f"{view}: {pixmap.height()}px-tall art cropped to a "
        f"{win.main_label.height()}px label"
    )

    win.hide()


def test_death_view_keeps_room_for_the_buttons(make_window, qapp):
    """The catch/defeat row is inside the window, not pushed past its foot.

    The death view is the tallest thing after the battle scene — the pokedex
    card plus the button row — and it is the view a fixed height is most
    likely to clip, since the buttons are added below the art rather than
    drawn into it.
    """
    win = make_window()
    win.show()
    qapp.processEvents()
    win.display_pokemon_death()
    qapp.processEvents()

    assert not win.button_widget.isHidden()
    button_bottom = win.button_widget.geometry().bottom()
    assert button_bottom <= win.height(), (
        f"catch/defeat row ends at y={button_bottom} in a {win.height()}px window"
    )

    win.hide()


def test_layout_identity_persists_across_display_calls(make_window):
    tracker = _FakeTracker()
    win = make_window(tracker=tracker)

    layout_before = win.layout()
    label_before = win.main_label

    win.display_first_encounter()
    win._last_display_time = 0  # step past the debounce window
    win.display_battle()

    # Same layout object, same label object, same child count — no rebuild.
    assert win.layout() is layout_before
    assert win.main_label is label_before
    assert win.layout().count() == 2
    assert win.current_view == "battle"
    assert not win.main_label.pixmap().isNull()


def test_hp_none_values_render_in_encounter_and_battle_views(make_window):
    main = _FakePokemon("pikachu", 25, hp=None, max_hp=None, type=["electric"])
    enemy = _FakePokemon("charizard", 6, hp=None, max_hp=None, type=["fire", "flying"])
    win = make_window(main=main, enemy=enemy)

    assert win._safe_hp_pair(None, None) == (0, 1)
    assert win._safe_hp_pair("invalid", 0) == (0, 1)

    win.display_first_encounter()
    assert win.current_view == "battle"
    assert not win.main_label.pixmap().isNull()

    win._last_display_time = 0
    win.display_battle()
    assert win.current_view == "battle"
    assert not win.main_label.pixmap().isNull()


def test_hp_values_are_clamped_to_normalized_maximum(make_window):
    win = make_window()

    assert win._safe_hp_pair(150, 100) == (100, 100)
    assert win._safe_hp_pair(10**400, 1) == (1, 1)


def test_first_encounter_resets_counter_and_battle_does_not_increment(make_window):
    tracker = _FakeTracker()
    tracker.pokemon_encounter = 3
    win = make_window(tracker=tracker)

    win.display_first_encounter()
    assert tracker.pokemon_encounter == 0  # exp: reset on first encounter

    win._last_display_time = 0
    win.display_battle()
    # exp removed the per-render increment; the battle loop owns the counter.
    assert tracker.pokemon_encounter == 0


def test_get_display_name_mega_gmax_and_localized(make_window):
    from Ankimon.functions.pokedex_functions import get_pretty_name_for_name

    win = make_window()

    cases = {
        "venusaur-mega": "Mega Venusaur",
        "charizard-mega-x": "Mega Charizard X",
        "charizard-gmax": "Gigantamax Charizard",
    }
    for internal, pretty in cases.items():
        # The oracle is the real pokedex.json via the base helper...
        assert get_pretty_name_for_name(internal) == pretty
        # ...and the window routes special forms through exactly that helper.
        assert win._get_display_name(_FakePokemon(internal, 3)) == pretty

    # Normal forms keep the localized-name path (English here).
    assert win._get_display_name(_FakePokemon("charizard", 6)) == "Charizard"


def test_battle_to_death_transition_is_never_debounced(make_window, monkeypatch):
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    win.display_first_encounter()
    win.display_battle()  # same instant: first battle render after encounter
    assert win.current_view == "battle"

    # Main's battle loop calls display_battle() and then the death screen in
    # the same tick on a faint — the death render must not be dropped.
    win.display_pokemon_death()
    assert win.current_view == "death"
    assert not win.button_widget.isHidden()
    assert win.kill_button.text() == win.translator.translate("defeat_button")
    assert win.catch_button.text() == win.translator.translate("catch_button")
    assert win.nickname_input.placeholderText() == win.translator.translate(
        "choose_nickname"
    )


def test_same_view_repeat_is_debounced(make_window, monkeypatch):
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    win.display_first_encounter()
    win.display_battle()

    renders = []
    monkeypatch.setattr(
        win, "pokemon_display_battle", lambda: renders.append(1) or win.main_label
    )

    win.display_battle()  # duplicate at the same instant -> dropped
    assert renders == []

    clock.advance(0.1)  # past the 50ms window -> renders again
    win.display_battle()
    assert renders == [1]

    # Duplicate death renders are debounced the same way.
    win.display_pokemon_death()
    assert win.current_view == "death"
    death_renders = []
    monkeypatch.setattr(
        win,
        "pokemon_display_dead_pokemon",
        lambda: (
            death_renders.append(1)
            or (win.main_label, win.kill_button, win.catch_button, win.nickname_input)
        ),
    )
    win.display_pokemon_death()
    assert death_renders == []


def test_new_encounter_clears_debounce_carryover(make_window, monkeypatch):
    """A fresh encounter's first battle render must never be debounced away.

    Encounter A stamps ``_last_display_time`` on its last battle render; if a
    new encounter B begins inside the 50 ms window, its ``display_first_encounter``
    must reset the timestamp so B's first ``display_battle`` still renders.
    """
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    # Encounter A: render battle, stamping the debounce timestamp at "now".
    win.display_first_encounter()
    win.display_battle()
    assert win.current_view == "battle"

    # Encounter B begins in the SAME tick (well inside the 50 ms window).
    win.display_first_encounter()

    renders = []
    monkeypatch.setattr(
        win, "pokemon_display_battle", lambda: renders.append(1) or win.main_label
    )
    win.display_battle()
    # Without the reset this render is dropped (renders == []).
    assert renders == [1]
    assert win.current_view == "battle"


def test_death_screen_survives_missing_pokedex_species_id(make_window, monkeypatch):
    """``search_pokedex`` returns ``[]`` on an unknown name; the death screen
    must not crash with ``int([])`` — it falls back to the species id."""
    win = make_window(enemy=_FakePokemon("nonexistentmon", 6))
    monkeypatch.setattr(
        sys.modules[_MODULE_NAME], "search_pokedex", lambda name, var: []
    )
    # Should render without raising TypeError.
    win.display_pokemon_death()
    assert win.current_view == "death"
    assert not win.main_label.pixmap().isNull()


def test_death_buttons_route_through_hook_registry_seam(make_window, monkeypatch):
    win = make_window()

    calls = []
    hook_registry = types.ModuleType("Ankimon.hook_registry")
    hook_registry.CatchPokemonHook = lambda ids: calls.append(("catch", set(ids)))
    hook_registry.DefeatPokemonHook = lambda: calls.append(("defeat",))
    reviewer_ui = types.ModuleType("Ankimon.reviewer_ui")
    reviewer_ui._collected_pokemon_ids = {6, 25}

    monkeypatch.setitem(sys.modules, "Ankimon.hook_registry", hook_registry)
    monkeypatch.setitem(sys.modules, "Ankimon.reviewer_ui", reviewer_ui)
    monkeypatch.setattr(
        sys.modules["Ankimon"], "hook_registry", hook_registry, raising=False
    )
    monkeypatch.setattr(
        sys.modules["Ankimon"], "reviewer_ui", reviewer_ui, raising=False
    )

    win.display_pokemon_death()
    assert win.windowTitle() != "Ankimon Window"  # death title shows catch_or_free

    win.catch_button.click()
    assert calls == [("catch", {6, 25})]
    assert win.windowTitle() == "Ankimon Window"  # reset before the callback runs

    win.kill_button.click()
    assert calls == [("catch", {6, 25}), ("defeat",)]

    # A second death render re-wires cleanly (disconnect + reconnect, no stacking).
    win._last_display_time = 0
    win.display_pokemon_death()
    calls.clear()
    win.catch_button.click()
    assert calls == [("catch", {6, 25})]


# --- issue #101: a missing sprite must not take the window down -------------
#
# `QPixmap.load` reports a missing/corrupt file by returning False rather than
# raising, so a sprite the user never downloaded left a NULL pixmap (width 0)
# and the aspect-ratio maths blew up with "integer division or modulo by zero".
# The reporter hit it whenever Scatterbug (id 664) was their main Pokemon —
# only the main's *back* sprite was absent.


@pytest.fixture
def missing_sprite(tmp_path):
    """A sprite path guaranteed not to exist.

    Deliberately NOT a path under ``user_files/sprites/`` — that directory is
    gitignored and gets populated by the real sprite download, so a developer
    who has fetched sprites would silently be testing a sprite that IS there.
    """
    path = tmp_path / "back_default" / "664.png"
    assert not path.exists()
    return path


@pytest.mark.parametrize(
    "render", ["pokemon_display_first_encounter", "pokemon_display_battle"]
)
@pytest.mark.parametrize("missing", ["main", "enemy", "both"])
def test_missing_sprite_renders_instead_of_dividing_by_zero(
    make_window, missing_sprite, render, missing
):
    """Both battle renders survive a sprite file that isn't on disk."""
    main = _FakePokemon(
        "scatterbug",
        664,
        type=["bug"],
        sprite_path=missing_sprite if missing in ("main", "both") else _REAL_SPRITE,
    )
    enemy = _FakePokemon(
        "charizard",
        6,
        type=["fire", "flying"],
        sprite_path=missing_sprite if missing in ("enemy", "both") else _REAL_SPRITE,
    )

    win = make_window(main=main, enemy=enemy)

    label = getattr(win, render)()  # used to raise ZeroDivisionError
    assert label is not None


def test_missing_sprite_falls_back_to_the_substitute_pixmap(
    make_window, missing_sprite
):
    """The fallback actually loads — the user sees a substitute, not a blank.

    The old ``try/except`` around ``QPixmap.load`` never fired, so the
    substitute was never reached. ``default_path`` is pointed at a real PNG
    here because the shipped substitute.png only lands in ``user_files`` after
    the runtime sprite download.
    """
    main = _FakePokemon("scatterbug", 664, sprite_path=missing_sprite)
    win = make_window(main=main)
    win.default_path = _REAL_SPRITE

    pixmap = win._load_sprite(main, "back")

    assert not pixmap.isNull()
    assert pixmap.width() > 0


def test_raising_sprite_lookup_still_reaches_the_substitute(make_window):
    """A ``get_sprite_path`` that blows up must fall back, as it always did."""

    class _Exploding(_FakePokemon):
        def get_sprite_path(self, side, ext):
            raise RuntimeError("pokedex lookup failed")

    main = _Exploding("scatterbug", 664)
    win = make_window(main=main)
    win.default_path = _REAL_SPRITE

    assert not win._load_sprite(main, "back").isNull()


def test_zero_max_hp_does_not_crash_the_hp_bar(make_window, tw_module):
    """A corrupt save carrying max_hp = 0 draws an empty bar, not a traceback."""
    from PyQt6.QtGui import QPainter, QPixmap

    win = make_window()
    canvas = QPixmap(100, 100)
    painter = QPainter(canvas)
    try:
        assert win.draw_hp_bar(0, 0, 8, 116, 0, 0, painter) is painter
    finally:
        painter.end()


@pytest.mark.parametrize(
    "render", ["pokemon_display_first_encounter", "pokemon_display_battle"]
)
def test_zero_experience_does_not_crash_the_xp_bar(
    make_window, tw_module, monkeypatch, render
):
    """A 0 from the exp-table lookup draws an empty XP bar, not a traceback.

    Patched at the module seam instead of asserting a particular
    ``find_experience_for_level`` return value, so this pins the divisor guard
    itself and stays valid however that lookup behaves — main clamps its
    sub-100 result to ``max(1, experience)``, so no real growth rate reaches 0
    today. Both renders compute the divisor (``window_show`` for the first
    encounter, ``pokemon_display_battle`` for the rest), so both are driven.
    """
    monkeypatch.setattr(tw_module, "find_experience_for_level", lambda *a, **kw: 0)

    # The growth rate is deliberately left at the default: the patch above makes
    # it irrelevant, and that is the point — the divisor is what is under test.
    win = make_window(main=_FakePokemon("scatterbug", 664, xp=10))

    assert getattr(win, render)() is not None


# ---------------------------------------------------------------------------
# _fit_sprite — bounded sprite scaling (fixes GIF/static sizing mismatch and
# the sprite overflowing into the message box).
# ---------------------------------------------------------------------------


def test_fit_sprite_scales_within_box_preserving_aspect(tw_module):
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(str(_REAL_SPRITE))
    assert not pixmap.isNull()

    fitted = tw_module.TestWindow._fit_sprite(pixmap, max_w=120, max_h=120)

    assert fitted.width() <= 120
    assert fitted.height() <= 120
    # At least one dimension should actually hit the box (not shrunk to
    # something tiny) — the scale is aspect-preserving, not distorting.
    assert fitted.width() == 120 or fitted.height() == 120


def test_fit_sprite_handles_a_null_pixmap_without_crashing(tw_module):
    from PyQt6.QtGui import QPixmap

    null_pixmap = QPixmap()
    assert null_pixmap.isNull()

    # Must not raise (a naive width/height divide would ZeroDivisionError).
    result = tw_module.TestWindow._fit_sprite(null_pixmap)
    assert result is null_pixmap


# ---------------------------------------------------------------------------
# Per-Pokémon attack shake — only the attacker's sprite offset should move,
# diagonally, settling back to (0, 0).
# ---------------------------------------------------------------------------


def test_shake_sprite_moves_only_the_named_side_diagonally(make_window, monkeypatch):
    win = make_window()
    win.show()

    # Run the QTimer.singleShot callback chain synchronously so the whole
    # shake sequence is observable without a real event loop.
    monkeypatch.setattr(
        sys.modules[_MODULE_NAME].QTimer,
        "singleShot",
        staticmethod(lambda _delay, fn: fn()),
    )

    assert win._enemy_shake_offset == (0, 0)
    assert win._main_shake_offset == (0, 0)

    win._shake_sprite("enemy")

    # Settles back to (0, 0) once the sequence completes.
    assert win._enemy_shake_offset == (0, 0)
    # The side that never attacked must never move.
    assert win._main_shake_offset == (0, 0)


def test_shake_sprite_offsets_are_diagonal_not_purely_horizontal(make_window, monkeypatch):
    win = make_window()
    win.show()

    seen_offsets = []
    monkeypatch.setattr(
        sys.modules[_MODULE_NAME].QTimer,
        "singleShot",
        staticmethod(lambda _delay, fn: (fn(), seen_offsets.append(win._main_shake_offset))),
    )

    win._shake_sprite("main")

    # At least one intermediate offset must have a non-zero y component —
    # a purely-horizontal shake (the old whole-window version) would fail this.
    assert any(dy != 0 for (_dx, dy) in seen_offsets)


# ---------------------------------------------------------------------------
# Battle-log text persistence — the message box used to go blank after the
# very first frame, since only the intro render ever drew into it.
# ---------------------------------------------------------------------------


def test_display_battle_updates_and_persists_the_message_text(make_window, monkeypatch):
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    win.display_first_encounter()
    assert win.last_message_text  # the intro frame seeds it

    win.display_battle(message_text="Pikachu used Thunderbolt!")
    assert win.last_message_text == "Pikachu used Thunderbolt!"

    # A later render with no new text keeps showing the last one — this is
    # the actual bug: it must NOT go back to blank.
    clock.advance(0.1)
    win.display_battle()
    assert win.last_message_text == "Pikachu used Thunderbolt!"


# ---------------------------------------------------------------------------
# Fainted sprite "tips over" — the classic Game Boy-era faint animation,
# rotated 90° around the sprite's own center rather than just standing there
# under an empty HP bar with nothing else to signal it fainted.
# ---------------------------------------------------------------------------


def test_draw_pokemon_sprite_rotates_90_degrees_when_fainted(make_window):
    win = make_window()
    rotations = []

    class _FakePainter:
        def save(self):
            pass

        def restore(self):
            pass

        def translate(self, x, y):
            pass

        def rotate(self, degrees):
            rotations.append(degrees)

        def drawPixmap(self, x, y, pixmap):
            pass

    # _draw_pokemon_sprite is a plain method — call it directly on an
    # unconstructed instance via the class, no QWidget/painter needed.
    win._draw_pokemon_sprite(
        _FakePainter(), object(), 0, 0, 40, 40,
        fainted=True, tip_direction=1,
    )

    assert rotations == [90]


def test_draw_pokemon_sprite_tips_the_other_way_for_the_other_side(make_window):
    win = make_window()
    rotations = []

    class _FakePainter:
        def save(self): pass
        def restore(self): pass
        def translate(self, x, y): pass
        def rotate(self, degrees): rotations.append(degrees)
        def drawPixmap(self, x, y, pixmap): pass

    win._draw_pokemon_sprite(
        _FakePainter(), object(), 0, 0, 40, 40,
        fainted=True, tip_direction=-1,
    )

    assert rotations == [-90]


def test_draw_pokemon_sprite_does_not_rotate_when_not_fainted(make_window):
    win = make_window()
    calls = []

    class _FakePainter:
        def save(self): calls.append("save")
        def restore(self): calls.append("restore")
        def translate(self, x, y): calls.append("translate")
        def rotate(self, degrees): calls.append("rotate")
        def drawPixmap(self, x, y, pixmap): calls.append(("drawPixmap", x, y))

    win._draw_pokemon_sprite(
        _FakePainter(), object(), 5, 7, 40, 40,
        fainted=False,
    )

    assert calls == [("drawPixmap", 5, 7)]


def test_pokemon_display_battle_tips_the_fainted_side(make_window):
    win = make_window(
        main=_FakePokemon("pikachu", 25, hp=0, max_hp=20),
        enemy=_FakePokemon("charizard", 6),
    )

    # Must not raise — the real regression risk here is a bad rotate-center
    # calculation blowing up the paint pass, not a specific pixel assertion.
    win.display_battle()
    assert not win.main_label.pixmap().isNull()


# ---------------------------------------------------------------------------
# force_display_battle — the one debounce-bypassing entry point. It replaces
# four copies of "reach in, zero the private _last_display_time, repaint"
# spread across three packages (drawing_utils, reviewer_obj, profile_data and
# this window's own shake steps).
# ---------------------------------------------------------------------------


def test_force_display_battle_bypasses_the_same_view_debounce(make_window, monkeypatch):
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    win.display_first_encounter()
    win.display_battle()

    renders = []
    monkeypatch.setattr(
        win, "pokemon_display_battle", lambda: renders.append(1) or win.main_label
    )

    win.display_battle(message_text="dropped")  # same instant -> debounced away
    assert renders == []

    win.force_display_battle(message_text="shown")
    assert renders == [1]
    assert win.last_message_text == "shown"


# ---------------------------------------------------------------------------
# paint_now — the faint frames the battle loop asks for are replaced inside the
# same synchronous call stack (the faint handler runs new_pokemon() ->
# display_first_encounter(), or the death screen, before Qt returns to its
# event loop), so a scheduled repaint would never actually reach the screen.
# ---------------------------------------------------------------------------


def test_paint_now_paints_the_frame_synchronously(make_window, monkeypatch):
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    painted = []
    monkeypatch.setattr(win.main_label, "repaint", lambda: painted.append(1))

    win.display_battle()
    assert painted == []  # ordinary turns just schedule, as before

    win.force_display_battle(message_text="Rattata fainted!", paint_now=True)
    assert painted == [1]
    assert win.last_message_text == "Rattata fainted!"


# ---------------------------------------------------------------------------
# Render cost on the reviewer's hot path. Every composite re-read both scene
# PNGs from disk and re-ran a SmoothTransformation scale of both sprites, and
# both sides shaking ran two independent timer chains — ~11 full composites for
# a single answered card.
# ---------------------------------------------------------------------------


def test_both_sides_shaking_share_one_render_chain(make_window, monkeypatch):
    win = make_window()
    win.show()

    monkeypatch.setattr(
        sys.modules[_MODULE_NAME].QTimer,
        "singleShot",
        staticmethod(lambda _delay, fn: fn()),
    )

    renders = []
    monkeypatch.setattr(
        win, "pokemon_display_battle", lambda: renders.append(1) or win.main_label
    )

    win.display_battle(message_text="both attacked", shake_enemy=True, shake_main=True)

    # 1 turn render + 5 animation steps. Two independent chains cost 11.
    assert len(renders) == 6
    assert win._enemy_shake_offset == (0, 0)
    assert win._main_shake_offset == (0, 0)

    win.hide()


def _count_sprite_loads(win, monkeypatch):
    """Record every real sprite decode the window performs."""
    loads = []
    real = win._load_sprite_checked
    monkeypatch.setattr(
        win,
        "_load_sprite_checked",
        lambda pokemon, side: (loads.append(side), real(pokemon, side))[1],
    )
    return loads


def test_sprite_scaling_is_memoized_across_repaints(make_window, monkeypatch):
    win = make_window()

    loads = _count_sprite_loads(win, monkeypatch)

    win.pokemon_display_battle()
    win.pokemon_display_battle()
    win.pokemon_display_battle()

    # One read per side for the whole session, not one per composite.
    assert sorted(loads) == ["back", "front"]


def test_a_missing_sprite_is_never_cached(make_window, missing_sprite, monkeypatch):
    """A sprite the user has not downloaded yet must not pin the substitute.

    ``_load_sprite`` silently substitutes for a file that isn't there, so
    caching that result would keep showing the substitute for the rest of the
    session even once the real artwork lands.
    """
    main = _FakePokemon("scatterbug", 664, sprite_path=missing_sprite)
    win = make_window(main=main)
    win.default_path = _REAL_SPRITE

    loads = _count_sprite_loads(win, monkeypatch)

    win.pokemon_display_battle()
    win.pokemon_display_battle()

    assert loads.count("back") == 2  # retried, not cached
    assert loads.count("front") == 1  # the enemy's real sprite still is


def test_an_unreadable_sprite_file_is_never_cached(make_window, tmp_path, monkeypatch):
    """A file that EXISTS but will not decode also falls back to the substitute.

    A path-exists check would call that a successful load and pin the
    substitute under the real sprite's key — the exact pinning the
    missing-file case is guarded against. The liveness test is whether the
    Pokémon's own sprite actually decoded, not whether a file is on disk.
    """
    corrupt = tmp_path / "back_default" / "664.png"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"this is not a PNG")
    assert corrupt.exists()

    main = _FakePokemon("scatterbug", 664, sprite_path=corrupt)
    win = make_window(main=main)
    win.default_path = _REAL_SPRITE

    loads = _count_sprite_loads(win, monkeypatch)

    win.pokemon_display_battle()
    win.pokemon_display_battle()

    assert loads.count("back") == 2  # retried, not pinned to the substitute
    assert loads.count("front") == 1


def test_static_scene_assets_are_decoded_once_per_window(make_window, tw_module):
    """The battle background is decoded once, not on every composite.

    Only assets that actually load are cached — a failed load must stay
    retryable, so ``_pixmap_cache`` holds whichever of the two scene assets
    resolved on this install rather than a fixed count.
    """
    win = make_window()

    win.pokemon_display_battle()
    cached = dict(win._pixmap_cache)

    background = str(
        tw_module.battlescene_path / win.ankimon_tracker_obj.battlescene_file
    )
    assert background in cached
    assert not cached[background].isNull()

    win.pokemon_display_battle()
    for key, pixmap in cached.items():
        assert win._pixmap_cache[key] is pixmap  # same decoded instance reused


# --- shake invalidation (row F51 follow-up) --------------------------------
#
# _shake_sprites() queues five QTimer.singleShot steps. Nothing holds a handle
# to them, so anything that replaces the scene inside that ~225 ms window
# leaves the tail of the chain still in flight, aimed at sprites that are no
# longer the ones that attacked.


def _drain_timers(qapp, ms=400):
    """Let the queued singleShot steps actually fire."""
    from PyQt6.QtCore import QDeadlineTimer, QEventLoop

    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)


def test_a_new_encounter_retires_the_previous_turn_s_shake(make_window, qapp):
    """The killing blow's shake must not follow the replacement Pokémon in.

    ``current_view`` alone cannot retire these steps: a faint calls
    ``new_pokemon()`` -> ``display_first_encounter()``, which sets the view
    straight back to ``"battle"``, so a step queued for the fight that just
    ended sails through the view check and jitters the fresh encounter for an
    attack it was never part of.
    """
    win = make_window()
    win.current_view = "battle"

    win._shake_sprites(["enemy"])
    win.display_first_encounter()

    painted = []
    win.force_display_battle = lambda *a, **k: painted.append(1)

    _drain_timers(qapp)

    assert painted == [], "a stale shake step repainted the new encounter"
    assert win._enemy_shake_offset == (0, 0)
    assert win._main_shake_offset == (0, 0)


def test_the_death_screen_is_not_repainted_by_an_in_flight_shake(make_window, qapp):
    """Manual mode leaves the death screen up until the player answers it."""
    win = make_window()
    win.current_view = "battle"

    win._shake_sprites(["main"])
    win.display_pokemon_death()
    assert win.current_view == "death"

    painted = []
    win.force_display_battle = lambda *a, **k: painted.append(1)

    _drain_timers(qapp)

    assert painted == []
    assert win.current_view == "death"


def test_closing_the_window_retires_a_shake_in_flight(make_window, qapp):
    win = make_window()
    win.current_view = "battle"

    win._shake_sprites(["enemy", "main"])
    win.close()

    painted = []
    win.force_display_battle = lambda *a, **k: painted.append(1)

    _drain_timers(qapp)

    assert painted == []
    # Offsets settle even though the chain's own settle step never ran — a
    # frozen mid-shake offset would render the sprites off-centre on reopen.
    assert win._enemy_shake_offset == (0, 0)
    assert win._main_shake_offset == (0, 0)


def test_an_uninterrupted_shake_still_animates_and_settles(make_window, qapp):
    """The guard must not switch the animation off altogether."""
    win = make_window()
    win.current_view = "battle"

    painted = []
    win.force_display_battle = lambda *a, **k: painted.append(
        (win._enemy_shake_offset, win._main_shake_offset)
    )

    win._shake_sprites(["enemy"])
    _drain_timers(qapp)

    assert len(painted) == 5, f"expected all five steps, got {painted}"
    assert any(offset != (0, 0) for offset, _ in painted), "nothing ever moved"
    assert win._enemy_shake_offset == (0, 0)  # settled
    assert win._main_shake_offset == (0, 0)   # never touched — enemy attacked


def test_a_debounced_render_still_keeps_the_new_battle_log_line(
    make_window, monkeypatch
):
    """The debounce suppresses the FRAME, not the text.

    ``display_battle()`` used to ``return`` on the debounce above the
    ``last_message_text`` assignment, so a call landing inside the 50 ms
    window threw that turn's log line away for good — ``formatted_battle_log``
    is a per-turn local the battle loop never re-sends, so the window kept
    showing the PREVIOUS turn's text. A shake step, a duplicate reviewer hook
    or an add-on reload all land inside that window.
    """
    win = make_window()
    clock = _FakeClock()
    monkeypatch.setattr(sys.modules[_MODULE_NAME], "time", clock)

    win.display_first_encounter()
    win.display_battle(message_text="Oshawott used Tackle!")
    assert win.last_message_text == "Oshawott used Tackle!"

    renders = []
    monkeypatch.setattr(
        win, "pokemon_display_battle", lambda: renders.append(1) or win.main_label
    )

    # Same instant -> the frame is dropped, but the text must still be kept.
    win.display_battle(message_text="Rattata used Quick Attack!")
    assert renders == [], "frame should still be debounced"
    assert win.last_message_text == "Rattata used Quick Attack!"

    # The very next repaint therefore shows the CURRENT line, not a stale one.
    clock.advance(0.1)
    win.display_battle()
    assert renders == [1]
    assert win.last_message_text == "Rattata used Quick Attack!"


def test_a_new_shake_retires_the_previous_turns_chain(make_window):
    """Consecutive turns must not share a shake generation.

    Nothing bumped ``_shake_generation`` between turns, so an in-flight chain
    from turn N and a new chain from turn N+1 both passed the staleness check;
    chain N's final settle-to-(0, 0) step then landed mid-animation and cut the
    new shake short, while its own steps kept writing a side that did not
    attack this turn.
    """
    win = make_window()
    win.display_first_encounter()

    win._shake_sprites(("main",))
    first_generation = win._shake_generation

    win._shake_sprites(("enemy",))
    assert win._shake_generation != first_generation, (
        "a new shake must invalidate the previous turn's queued steps"
    )

    # Retiring the old chain also settles both sprites, so the side the new
    # chain does not cover cannot stay frozen at a mid-shake offset.
    assert win._main_shake_offset == (0, 0)
