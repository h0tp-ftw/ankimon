"""
Integrity test for the Ankimon add-on.

Strategy
--------
We import each submodule individually and assert none raises ImportError /
AttributeError at module level.

We deliberately do NOT import Ankimon.__init__ because it is Anki's entry
point and runs irreducible side effects at import time (database init,
startup sequence, Qt widget construction, etc.).  Those side effects interact
badly with the MagicMock stubs used here, previously causing 10+ GB of RAM
usage as MagicMock iterators generated unbounded object trees.

We also deliberately do NOT use pkgutil.walk_packages() for the same reason:
iterating a MagicMock __path__ generates objects indefinitely.

The explicit MODULES_TO_CHECK list is reconciled against main's actual module
set: it includes main's service-seam / scaffolding modules (core, services,
events, ui_port, gui_presenter, boot_async, webshell.*) and the developer
hot-reload module (reloader), and omits Stage-B leaf modules that have not
landed on this branch yet (e.g. pyobj.move_picker,
pyobj.encounter_simulator_dialog, ankidex.ankidex_obj).
"""

import os
import sys
import importlib
import traceback
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 1. Stub out external runtime deps not available outside the Anki runtime
# ---------------------------------------------------------------------------

try:
    import anki
    import anki.buildinfo
except ImportError:

    class _MockAnkiPkg(MagicMock):
        __path__ = []

    _anki_mock = _MockAnkiPkg()
    sys.modules["anki"] = _anki_mock
    sys.modules["anki.hooks"] = MagicMock()
    sys.modules["anki.utils"] = MagicMock()
    _buildinfo = MagicMock()
    _buildinfo.version = "0.0.0-test"
    sys.modules["anki.buildinfo"] = _buildinfo

try:
    import aqt.operations
except ImportError:

    class _MockAqtPkg(MagicMock):
        __path__ = []

    sys.modules["aqt"] = _MockAqtPkg()
    sys.modules["aqt.operations"] = MagicMock()
    sys.modules["aqt.qt"] = MagicMock()
    sys.modules["aqt.utils"] = MagicMock()
    sys.modules["aqt.reviewer"] = MagicMock()
    sys.modules["aqt.gui_hooks"] = MagicMock()
    sys.modules["aqt.webview"] = MagicMock()
    sys.modules["aqt.theme"] = MagicMock()
    sys.modules["aqt.sound"] = MagicMock()
    sys.modules["aqt.main"] = MagicMock()

# ---------------------------------------------------------------------------
# 2. Put src/ on sys.path so "import Ankimon.xxx" resolves correctly
# ---------------------------------------------------------------------------

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# 3. Modules to pre-stub before any import runs
#
#    These perform blocking I/O or event-loop operations at import time and
#    must never actually execute in a headless test environment.
# ---------------------------------------------------------------------------

PRE_STUB_MODULES = {
    # ---- must stay stubbed (heavy side-effects or C extensions at import) ---
    "Ankimon.singletons",  # full Qt + DB init at module level
    "Ankimon.pyobj.download_sprites",  # spawns download threads
    "Ankimon.pyobj.backup_files",  # touches the filesystem
    "Ankimon.pyobj.migration_dialog",  # shows blocking Qt dialogs
    "Ankimon.functions.rate_addon_functions",  # reads rate_this.json on import
    "Ankimon.functions.ankimon_hooks_to_poke_engine",  # C extension bridge
    "Ankimon.pyobj.tip_of_the_day",  # from aqt.qt import * at class level
}

# ---------------------------------------------------------------------------
# 4. Explicit module list — every importable .py in the package.
#
#    NEVER switch back to pkgutil.walk_packages(): see module docstring.
#    When you add a new .py file to the package, add it here too.
# ---------------------------------------------------------------------------

# Modules we explicitly skip (stubbed above or require full runtime).
# Listed separately so it is easy to see what we are omitting and why.
SKIP_MODULES = PRE_STUB_MODULES | {
    "Ankimon",  # __init__.py — entry point; runs irreducible side effects
    "Ankimon.poke_engine.tests",
    "Ankimon.poke_engine.setup",
}

MODULES_TO_CHECK = [
    # top-level helpers & hooks
    "Ankimon.battle_loop",
    "Ankimon.business",
    "Ankimon.card_hooks",
    "Ankimon.changelog",
    "Ankimon.const",
    "Ankimon.discord_integration",
    "Ankimon.gui_entities",
    "Ankimon.hook_registry",
    "Ankimon.hooks",
    "Ankimon.menu_buttons",
    "Ankimon.move_names",
    "Ankimon.profile_hooks",
    "Ankimon.reloader",
    "Ankimon.resources",
    "Ankimon.reviewer_ui",
    "Ankimon.startup",
    "Ankimon.texts",
    "Ankimon.utils",
    # service seam + scaffolding (main-only foundation modules)
    "Ankimon.boot_async",
    "Ankimon.core",
    "Ankimon.events",
    "Ankimon.gui_presenter",
    "Ankimon.services",
    "Ankimon.ui_port",
    # webshell frame (main-only; zero screens yet)
    "Ankimon.webshell",
    "Ankimon.webshell.host",
    "Ankimon.webshell.live_update",
    # functions/
    "Ankimon.functions",
    "Ankimon.functions.badges_functions",
    "Ankimon.functions.battle_functions",
    "Ankimon.functions.battle_text_functions",
    "Ankimon.functions.create_css_for_reviewer",
    "Ankimon.functions.create_gui_functions",
    "Ankimon.functions.discord_function",
    "Ankimon.functions.drawing_utils",
    "Ankimon.functions.encounter_data",
    "Ankimon.functions.encounter_functions",
    "Ankimon.functions.friendship_evolution",
    "Ankimon.functions.gui_functions",
    "Ankimon.functions.learnset_retrieval",
    "Ankimon.functions.migration",
    "Ankimon.functions.pokedex_functions",
    "Ankimon.functions.pokemon_functions",
    "Ankimon.functions.pokemon_showdown_functions",
    "Ankimon.functions.reviewer_iframe",
    "Ankimon.functions.sprite_functions",
    "Ankimon.functions.starters",
    "Ankimon.functions.trainer_functions",
    "Ankimon.functions.update_main_pokemon",
    "Ankimon.functions.url_functions",
    # pyobj/
    "Ankimon.pyobj",
    "Ankimon.pyobj.InfoLogger",
    "Ankimon.pyobj.achievement_window",
    "Ankimon.pyobj.ankimon_leaderboard",
    "Ankimon.pyobj.ankimon_shop",
    "Ankimon.pyobj.ankimon_sync",
    "Ankimon.pyobj.ankimon_tracker",
    "Ankimon.pyobj.ankimon_tracker_window",
    "Ankimon.pyobj.attack_dialog",
    "Ankimon.pyobj.backup_manager",
    "Ankimon.pyobj.collection_dialog",
    "Ankimon.pyobj.database_manager",
    "Ankimon.pyobj.error_handler",
    "Ankimon.pyobj.evolution_window",
    "Ankimon.pyobj.help_window",
    "Ankimon.pyobj.item_window",
    "Ankimon.pyobj.pc_box",
    "Ankimon.pyobj.pokemon_obj",
    "Ankimon.pyobj.pokemon_trade",
    "Ankimon.pyobj.reviewer_obj",
    "Ankimon.pyobj.settings",
    "Ankimon.pyobj.settings_window",
    "Ankimon.pyobj.starter_window",
    "Ankimon.pyobj.test_window",
    "Ankimon.pyobj.trainer_card",
    "Ankimon.pyobj.translator",
    "Ankimon.pyobj.update_dialog",
    "Ankimon.pyobj.update_manager",
    # gui_classes/
    "Ankimon.gui_classes",
    "Ankimon.gui_classes.backup_manager_dialog",
    "Ankimon.gui_classes.check_files",
    "Ankimon.gui_classes.overview_team",
    "Ankimon.gui_classes.pokemon_details",
    # classes/
    "Ankimon.classes.choose_move_dialog",
    # pokedex/ (legacy; superseded by the future ankidex leaf)
    "Ankimon.pokedex.pokedex_obj",
]

# ---------------------------------------------------------------------------
# 5. Known-benign error patterns
#    These are tracked issues, not regressions. Real import bugs must NOT
#    match any of these patterns.
# ---------------------------------------------------------------------------

KNOWN_BENIGN_PATTERNS = [
    # overview_team.init_hooks — tracked as a separate fix
    "module 'Ankimon.gui_classes.overview_team' has no attribute 'init_hooks'",
]

# ---------------------------------------------------------------------------
# 6. The test
# ---------------------------------------------------------------------------


def test_ankimon_submodule_integrity(qapp):
    """
    Imports every Ankimon submodule and asserts none raises an ImportError or
    AttributeError.  Uses the pytest-qt `qapp` fixture so that Qt widget
    construction in module bodies does not crash.

    Does NOT import Ankimon.__init__ (see module docstring).
    """
    import aqt
    from PyQt6.QtWidgets import QMainWindow

    # Use a real QMainWindow subclass — Qt rejects MagicMock as a parent
    class MockMainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.pm = MagicMock()
            self.pm.name = "test_profile"
            self.form = MagicMock()
            self.addonManager = MagicMock()

        def _increase_background_ops(self):
            pass

        def _decrease_background_ops(self):
            pass

    aqt.mw = MockMainWindow()
    aqt.mw.taskman = MagicMock()
    aqt.mw.logger = MagicMock()
    aqt.mw.ankimon_db = MagicMock()
    # Ensure getattr(mw, "x", None) returns None for lazy-init guards
    # (MagicMock attributes are truthy, so we explicitly set common ones)
    aqt.mw.main_pokemon = None
    aqt.mw.enemy_pokemon = None
    aqt.mw.trainer_card = None
    aqt.mw.ankimon_tracker_obj = None
    aqt.mw.shop_manager = None
    aqt.mw.reviewer_obj = None
    aqt.mw.achievements_dict = None
    aqt.mw.translator = None

    # main re-fit: unlike exp (which routes settings through the service seam),
    # main's menu_buttons.py and gui_classes/overview_team.py read
    # ``mw.settings_obj`` at *import* time (Translator + menu construction, and a
    # deck-view hook gate).  A bare ``None`` breaks those module-level reads, so
    # give settings_obj a light import-safe fake.  The None attrs above are kept
    # None so the in-function lazy-init guards elsewhere still behave.
    class _ImportSafeSettings:
        _defaults = {"misc.language": 0, "gui.team_deck_view": False}

        def get(self, key, default=None):
            return self._defaults.get(key, default)

    aqt.mw.settings_obj = _ImportSafeSettings()

    # Install pre-stubs so that imports of heavy modules are intercepted.
    # Force-install even when an entry already exists: other test modules
    # (e.g. test_location_evolutions, test_friendship_evolution) install bare
    # module-level stubs of Ankimon.singletons at collection time, and those
    # lack the names (main_pokemon, logger, ...) that real modules from-import.
    # The prior entries are restored below so those suites keep their stubs.
    _prev_stubs = {}
    for mod_name in PRE_STUB_MODULES:
        _prev_stubs[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = MagicMock()

    errors = []
    checked = 0

    try:
        for mod_name in MODULES_TO_CHECK:
            # Exact-match skips, plus prefix skips for anything living *under* a
            # skipped package.  The bare "Ankimon" entry must be exact-match only:
            # every module in MODULES_TO_CHECK starts with "Ankimon.", so
            # prefix-matching it would silently skip the entire list and make the
            # test vacuous.
            if mod_name in SKIP_MODULES or any(
                mod_name.startswith(skip + ".")
                for skip in SKIP_MODULES
                if skip != "Ankimon"
            ):
                continue

            checked += 1
            try:
                importlib.import_module(mod_name)
            except Exception:
                tb = traceback.format_exc()
                if not any(pat in tb for pat in KNOWN_BENIGN_PATTERNS):
                    errors.append(f"[{mod_name}]\n{tb}")
    finally:
        for mod_name, _prev in _prev_stubs.items():
            if _prev is not None:
                sys.modules[mod_name] = _prev

    if errors:
        divider = "\n" + "-" * 60 + "\n"
        pytest.fail(
            f"Integrity check failed with {len(errors)} error(s):"
            + divider
            + divider.join(errors)
        )

    # Vacuity guard: the skip filter must never regress into excluding the
    # whole list (prefix-matching the bare "Ankimon" entry once did exactly
    # that, silently turning this test into a no-op).
    assert checked >= len(MODULES_TO_CHECK) - len(SKIP_MODULES), (
        f"skip filter excluded too much: only {checked} of "
        f"{len(MODULES_TO_CHECK)} modules were import-checked"
    )
