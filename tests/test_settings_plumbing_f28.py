"""Characterization tests for the F28 settings plumbing + Stage-B config keys.

F28 ports BRRRR_Experimental's settings-schema plumbing (the scaffolding that
the auto-catch / region / team-cycle / mobile config keys depend on) onto main's
service-seam ``Settings`` (``services.db``, not ``aqt.mw``). These tests pin the
observable contract of that plumbing so a regression is caught:

  * the new DEFAULT_CONFIG keys exist (and main's economy schema is preserved,
    NOT overwritten with exp's amount=100 / missing earned-today values);
  * ``get()`` resolves an unset (None) value to the caller default and then to
    the DEFAULT_CONFIG value (the fallback new keys such as misc.active_region
    rely on);
  * ``load_config`` treats a stored ``None`` as "unset" and reseeds it from
    DEFAULT_CONFIG — but only when the schema default itself is not None, so
    a None-default key (misc.active_region) cannot flag the config modified
    (and rewrite it to the DB) on every load;
  * loading twice writes nothing on the second load (no startup churn), and a
    persisted None for misc.active_region survives the real DB's str() scalar
    encoding (which stores it as the string "None") as a real ``None``;
  * ``controls.team_cycle_count`` is int-coerced on load;
  * ``save_config`` preserves the identity of the ``self.config`` dict
    (clear/update in place) so external holders keep observing updates;
  * ``get('evolution.friendship_time_enabled')`` stays a real user toggle — exp's
    hardcoded ``return True`` override is deliberately NOT ported.

The exp-shipped ``tests/test_wishlist_serialization.py`` targets
``ankimon_items_web.shop_obj.AnkimonItemsWeb._serialize_setting`` — a web-shell
leaf owned by a different inventory row (not present on main), so it is left for
that row rather than ported here.

Runs Qt-free (Tier-1 / venv_t1): ``Settings`` imports only ``json``/``os``/
``shutil`` + the ``services`` registry, never ``aqt``.
"""

import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


def _install_aqt_stubs(mw):
    """Install fake aqt/PyQt6 modules and a configurable mw singleton."""
    aqt_stub = types.ModuleType("aqt")
    aqt_stub.mw = mw
    sys.modules["aqt"] = aqt_stub

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showInfo = lambda *a, **k: None
    aqt_utils.tooltip = lambda *a, **k: None
    aqt_utils.showWarning = lambda *a, **k: None
    sys.modules["aqt.utils"] = aqt_utils

    aqt_qt = types.ModuleType("aqt.qt")
    for name in (
        "QWidget",
        "QVBoxLayout",
        "QLabel",
        "QLineEdit",
        "QPushButton",
        "QRadioButton",
        "QHBoxLayout",
        "QMainWindow",
        "QScrollArea",
        "QButtonGroup",
        "QComboBox",
        "QMessageBox",
        "QPixmap",
        "QPainter",
        "QPainterPath",
        "Qt",
        "QRectF",
    ):
        setattr(aqt_qt, name, MagicMock())
    sys.modules["aqt.qt"] = aqt_qt


def _load_module(dotted_name, file_path):
    """Load a module from a file path under a specific dotted name."""
    spec = importlib.util.spec_from_file_location(dotted_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeDB:
    """Minimal config-only DB double for exercising load_config branches
    without depending on the real SQLite value-encoding (which stores Python
    None as the string "None")."""

    def __init__(self, cfg):
        self._cfg = dict(cfg)
        self.saved = None
        self.save_count = 0

    def has_config(self):
        return bool(self._cfg)

    def get_all_config(self):
        return dict(self._cfg)

    def save_all_config(self, cfg):
        self.saved = dict(cfg)
        self._cfg = dict(cfg)
        self.save_count += 1
        return True

    def set_config_value(self, key, value):
        self._cfg[key] = value
        return True


@pytest.fixture
def isolated_env(tmp_path):
    """Fresh module namespace + temp user_path for each test."""
    for mod_name in list(sys.modules):
        if mod_name.startswith("Ankimon") or mod_name in (
            "aqt",
            "aqt.qt",
            "aqt.utils",
        ):
            del sys.modules[mod_name]

    user_path = tmp_path / "user_files"
    user_path.mkdir()

    class FakeMw:
        pass

    _install_aqt_stubs(FakeMw())

    ankimon_pkg = types.ModuleType("Ankimon")
    ankimon_pkg.__path__ = [str(_src / "Ankimon")]
    sys.modules["Ankimon"] = ankimon_pkg

    pyobj_pkg = types.ModuleType("Ankimon.pyobj")
    pyobj_pkg.__path__ = [str(_src / "Ankimon" / "pyobj")]
    sys.modules["Ankimon.pyobj"] = pyobj_pkg

    resources_stub = types.ModuleType("Ankimon.resources")
    resources_stub.user_path = user_path
    resources_stub.csv_file_items_cost = tmp_path / "items.csv"
    resources_stub.mypokemon_path = tmp_path / "mypokemon.json"
    resources_stub.mainpokemon_path = tmp_path / "mainpokemon.json"
    resources_stub.items_path = tmp_path / "items.json"
    resources_stub.badges_path = tmp_path / "badges.json"
    resources_stub.team_pokemon_path = tmp_path / "team.json"
    sys.modules["Ankimon.resources"] = resources_stub

    db_mod = _load_module(
        "Ankimon.pyobj.database_manager",
        _src / "Ankimon" / "pyobj" / "database_manager.py",
    )

    # Settings imports ankimon_sync lazily inside load/save_config — stub it so
    # the obfuscation fallback path never drags in aqt.
    sync_stub = types.ModuleType("Ankimon.pyobj.ankimon_sync")

    class _NoSync:
        def _obfuscate_data(self, data):
            return ""

        def _deobfuscate_data(self, s):
            return {}

    sync_stub.AnkimonDataSync = _NoSync
    sys.modules["Ankimon.pyobj.ankimon_sync"] = sync_stub

    settings_mod = _load_module(
        "Ankimon.pyobj.settings",
        _src / "Ankimon" / "pyobj" / "settings.py",
    )

    return {
        "user_path": user_path,
        "db_mod": db_mod,
        "settings_mod": settings_mod,
        "services": settings_mod.services,
    }


def _settings_with_real_db(env):
    db = env["db_mod"].AnkimonDB()
    env["services"].db = db
    return env["settings_mod"].Settings(), db


def test_new_default_config_keys_present(isolated_env):
    cfg = isolated_env["settings_mod"].DEFAULT_CONFIG
    for key in (
        "battle.auto_catch_legendary",
        "battle.auto_catch_mythical",
        "battle.auto_catch_ultra",
        "battle.auto_catch_starter",
        "battle.auto_catch_mega",
        "battle.auto_catch_gmax",
        "battle.auto_catch_regional",
        "controls.team_cycle_key",
        "controls.team_cycle_count",
        "misc.active_region",
        "mobile.enabled",
        "mobile.resolution_mode",
        "mobile.inactive_companions",
        "trainer.mobile_reviews_resolved_since_payout",
    ):
        assert key in cfg, f"DEFAULT_CONFIG missing new F28 key {key!r}"

    assert cfg["battle.auto_catch_wishlist"] == [25, 133]
    assert cfg["misc.active_region"] is None
    assert cfg["controls.team_cycle_key"] == "9"
    assert cfg["controls.team_cycle_count"] == 3


def test_main_economy_schema_preserved(isolated_env):
    """GUARD: keep main's cash-reward design (amount=40 + earned-today/last-date);
    do NOT regress to exp's amount=100 / missing keys."""
    cfg = isolated_env["settings_mod"].DEFAULT_CONFIG
    assert cfg["trainer.cash_reward_amount"] == 40
    assert cfg["trainer.cash_reward_interval"] == 10
    assert cfg["trainer.cash_earned_today"] == 0
    assert cfg["trainer.last_cash_reward_date"] == ""


def test_get_falls_back_to_default_config(isolated_env):
    settings, _ = _settings_with_real_db(isolated_env)
    # Unset (None) misc.active_region resolves to its schema default (None).
    assert settings.get("misc.active_region") is None
    # A stored default value is returned as-is.
    assert settings.get("controls.team_cycle_count") == 3
    assert settings.get("battle.auto_catch_legendary") is True
    # Caller default wins over DEFAULT_CONFIG for an unknown key.
    assert settings.get("does.not.exist", "fallback") == "fallback"
    # An in-memory None is treated as unset -> DEFAULT_CONFIG value.
    settings.config["battle.auto_catch_mega"] = None
    assert settings.get("battle.auto_catch_mega") is True


def test_load_config_reseeds_none_value_from_default(isolated_env):
    env = isolated_env
    env["services"].db = _FakeDB({"misc.gen9": None, "controls.team_cycle_count": 3})
    settings = env["settings_mod"].Settings()
    # A persisted None for gen9 is reseeded from DEFAULT_CONFIG (False).
    assert settings.config["misc.gen9"] is False


def test_load_config_second_load_writes_nothing(isolated_env):
    """Loading an already-seeded config must not rewrite it: a None-default
    key (misc.active_region) must not re-trigger the modified->save path on
    every load (startup churn)."""
    env = isolated_env
    db = _FakeDB({})
    env["services"].db = db
    settings = env["settings_mod"].Settings()  # first load seeds the defaults
    assert db.save_count == 1
    assert settings.config["misc.active_region"] is None
    settings.load_config()
    assert db.save_count == 1, "second load must not write the config again"
    assert settings.config["misc.active_region"] is None


def test_load_config_idempotent_against_real_db_none_encoding(isolated_env):
    """Same churn guarantee against the real AnkimonDB, whose scalar encoding
    stores Python None as the string "None": the value must come back as a
    real None and the second load must not save."""
    env = isolated_env
    settings, db = _settings_with_real_db(env)  # __init__ seeded + saved once
    save_calls = []
    original_save = db.save_all_config
    db.save_all_config = lambda cfg: save_calls.append(1) or original_save(cfg)
    settings.load_config()
    assert save_calls == [], "second load must not write the config again"
    assert settings.config["misc.active_region"] is None
    assert settings.get("misc.active_region") is None


def test_active_region_none_survives_db_roundtrip(isolated_env):
    """An explicit None (user picked "No Region") survives persistence: the
    DB hands back the string "None", which load_config normalizes to None."""
    env = isolated_env
    settings, _ = _settings_with_real_db(env)
    settings.set("misc.active_region", "kanto")
    settings.set("misc.active_region", None)
    env["services"].db = env["db_mod"].AnkimonDB()  # simulate a restart
    reloaded = env["settings_mod"].Settings()
    assert reloaded.config["misc.active_region"] is None
    assert reloaded.get("misc.active_region") is None


def test_team_cycle_count_str_coerced_to_int(isolated_env):
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    db.save_all_config({"controls.team_cycle_count": "5"})
    env["services"].db = db
    settings = env["settings_mod"].Settings()
    assert settings.get("controls.team_cycle_count") == 5
    assert type(settings.get("controls.team_cycle_count")) is int


def test_save_config_preserves_dict_identity(isolated_env):
    settings, _ = _settings_with_real_db(isolated_env)
    original_ref = settings.config
    new_cfg = dict(settings.config)
    new_cfg["battle.auto_catch_legendary"] = False
    settings.save_config(new_cfg)
    assert settings.config is original_ref, "config dict identity was not preserved"
    assert settings.config["battle.auto_catch_legendary"] is False


def test_new_keys_roundtrip_through_db(isolated_env):
    env = isolated_env
    settings, _ = _settings_with_real_db(env)
    settings.set("misc.active_region", "kanto")
    settings.set("battle.auto_catch_wishlist", [1, 4, 7])
    # Fresh AnkimonDB simulates a restart reading the same store.
    cfg = env["db_mod"].AnkimonDB().get_all_config()
    assert cfg["misc.active_region"] == "kanto"
    assert cfg["battle.auto_catch_wishlist"] == [1, 4, 7]


def test_friendship_time_enabled_is_a_real_toggle(isolated_env):
    """Exp hardcoded get('evolution.friendship_time_enabled') -> True; that
    override is NOT ported, so the key stays user-toggleable."""
    settings, _ = _settings_with_real_db(isolated_env)
    assert settings.get("evolution.friendship_time_enabled") is True
    settings.set("evolution.friendship_time_enabled", False)
    assert settings.get("evolution.friendship_time_enabled") is False


def test_sprite_visibility_default_migration_preserves_existing_hud_preferences(
    isolated_env,
):
    env = isolated_env
    db = _FakeDB(
        {
            "gui.hud_player_sprite": False,
            "gui.hud_enemy_sprite": True,
        }
    )
    env["services"].db = db

    settings = env["settings_mod"].Settings()

    assert settings.config["gui.show_sprites_across_ankimon"] is True
    assert settings.config["gui.hud_player_sprite"] is False
    assert db.saved["gui.hud_player_sprite"] is False


def test_sprite_visibility_autosync_honors_explicit_same_save_override(isolated_env):
    settings_mod = isolated_env["settings_mod"]
    settings = object.__new__(settings_mod.Settings)
    player_key = "gui.hud_player_sprite"
    config = {
        "gui.show_sprites_across_ankimon": False,
        **{key: True for key in settings_mod.HUD_TOGGLE_AUTO_SYNC_KEYS},
    }

    changed = settings._apply_hud_toggle_autosync(
        config,
        previous_value=True,
        explicit_overrides={player_key},
    )

    assert config[player_key] is True
    assert player_key not in changed
    for key in settings_mod.HUD_TOGGLE_AUTO_SYNC_KEYS:
        if key != player_key:
            assert config[key] is False


def test_gif_visibility_autosync_and_explicit_reviewer_override(isolated_env):
    settings_mod = isolated_env["settings_mod"]
    settings = object.__new__(settings_mod.Settings)
    config = {
        "gui.gif_in_collection": False,
        "gui.reviewer_image_gif": True,
        "ankidex.spriteMode": "animated",
    }

    changed = settings._apply_gif_toggle_autosync(config, previous_value=True)

    assert changed == ["gui.reviewer_image_gif", "ankidex.spriteMode"]
    assert config["gui.reviewer_image_gif"] is False
    assert config["ankidex.spriteMode"] == "static"

    config["gui.gif_in_collection"] = True
    config["gui.reviewer_image_gif"] = False
    config["ankidex.spriteMode"] = "animated"
    changed = settings._apply_gif_toggle_autosync(
        config,
        previous_value=False,
        explicit_overrides={"gui.reviewer_image_gif"},
    )

    assert config["gui.reviewer_image_gif"] is False
    assert config["ankidex.spriteMode"] == "animated"
    assert changed == []


def test_gif_visibility_preserves_an_explicit_web_mode_override(isolated_env):
    settings_mod = isolated_env["settings_mod"]
    settings = object.__new__(settings_mod.Settings)
    config = {
        "gui.gif_in_collection": False,
        "gui.gif_mode_override": True,
        "gui.reviewer_image_gif": True,
        "ankidex.spriteMode": "animated",
    }

    changed = settings._apply_gif_toggle_autosync(config, previous_value=True)

    assert config["gui.reviewer_image_gif"] is False
    assert config["ankidex.spriteMode"] == "animated"
    assert changed == ["gui.reviewer_image_gif"]


@pytest.mark.parametrize("legacy_mode", ["static", "animated"])
def test_legacy_pokedex_sprite_mode_migrates_before_gif_autosync(
    isolated_env, legacy_mode
):
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    db.save_all_config(
        {
            "pokedex_v2.spriteMode": legacy_mode,
            "gui.gif_in_collection": False,
        }
    )
    env["services"].db = db

    settings = env["settings_mod"].Settings()

    assert settings.config["ankidex.spriteMode"] == legacy_mode
    assert settings.config["gui.gif_mode_override"] is True
    settings.set("gui.gif_in_collection", True)
    assert settings.config["ankidex.spriteMode"] == legacy_mode


def test_current_ankidex_sprite_mode_wins_over_legacy_value(isolated_env):
    env = isolated_env
    db = env["db_mod"].AnkimonDB()
    db.save_all_config(
        {
            "pokedex_v2.spriteMode": "static",
            "ankidex.spriteMode": "animated",
        }
    )
    env["services"].db = db

    settings = env["settings_mod"].Settings()

    assert settings.config["ankidex.spriteMode"] == "animated"
    assert settings.config["gui.gif_mode_override"] is True


def test_unchanged_ankidex_mode_save_preserves_override(isolated_env):
    settings, _ = _settings_with_real_db(isolated_env)
    settings.set("ankidex.spriteMode", "static")
    assert settings.config["gui.gif_mode_override"] is True

    settings.set("ankidex.spriteMode", "static")
    assert settings.config["gui.gif_mode_override"] is True

    settings.set("ankidex.spriteMode", "animated")
    assert settings.config["gui.gif_mode_override"] is False


def test_sprite_visibility_web_override_metadata_and_scope_are_wired():
    settings_js = (
        _src / "Ankimon" / "ankimon_items_web" / "settings.js"
    ).read_text(encoding="utf-8")
    shop_obj = (
        _src / "Ankimon" / "ankimon_items_web" / "shop_obj.py"
    ).read_text(encoding="utf-8")

    assert "explicitHudOverrides: new Set()" in settings_js
    assert (
        "explicit_hud_overrides: Array.from(state.explicitHudOverrides)"
        in settings_js
    )
    assert "state.explicitHudOverrides.add(setting.key)" in settings_js

    allowlist = shop_obj.split("SPRITE_VISIBILITY_SCREENS = (", 1)[1].split(
        ")", 1
    )[0]
    assert "SCREEN_ITEMS" in allowlist
    assert "SCREEN_ANKIDEX" in allowlist
    assert "SCREEN_PROFILE" in allowlist
    assert "SCREEN_TEAM" in allowlist
    assert "SCREEN_MOBILE" in allowlist
    assert "SCREEN_HISTORY" in allowlist


def test_gif_visibility_ui_and_setting_copy_are_wired():
    settings_js = (
        _src / "Ankimon" / "ankimon_items_web" / "settings.js"
    ).read_text(encoding="utf-8")
    names = json.loads(
        (_src / "Ankimon" / "lang" / "setting_name.json").read_text(
            encoding="utf-8"
        )
    )
    descriptions = json.loads(
        (_src / "Ankimon" / "lang" / "setting_description.json").read_text(
            encoding="utf-8"
        )
    )

    assert names["gui.gif_in_collection"] == "Show GIFs Across Ankimon"
    assert descriptions["gui.gif_in_collection"] == (
        "Show animated sprites (GIFs) instead of static sprites in the PC box, "
        "during reviews, and other windows."
    )
    assert descriptions["gui.show_sprites_across_ankimon"] == (
        "Disable this setting for a text-based presentation. This hides all "
        "sprites, GIFs, and HP/XP bars, except those in the Ankimon Window "
        "(Ctrl+Shift +P)."
    )
    assert "syncGifTogglesForGifVisibility()" in settings_js
    assert "explicit_gif_overrides" in settings_js


def _load_settings_schema():
    schema_path = _src / "Ankimon" / "ankimon_items_web" / "settings_schema.py"
    spec = importlib.util.spec_from_file_location(
        "_ankimon_settings_schema_f28", schema_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schema_chip_keys(schema):
    keys = set()
    for group in schema.GROUPS:
        containers = [group, *group.get("subgroups", [])]
        for container in containers:
            chip_def = container.get("chip_group")
            if not chip_def:
                continue
            keys.update(key for key, _label in chip_def["keys"])
    return keys


def test_sprite_visibility_sync_repaints_the_chip_controls():
    """The HUD auto-sync keys are chips, so the post-sync repaint must find them.

    Toggling "Show Sprites Across Ankimon" rewrites every HUD element key in
    ``state.edits``. Each of those keys is rendered as a ``.setting-chip``
    button inside one shared chip row, never as its own ``.setting-row`` with a
    ``.setting-toggle``, so a repaint that only walks setting rows matches
    nothing and leaves the whole HUD block visually stale while the dirty
    counter climbs.
    """
    settings_js = (
        _src / "Ankimon" / "ankimon_items_web" / "settings.js"
    ).read_text(encoding="utf-8")
    schema = _load_settings_schema()

    js_keys = set(
        re.findall(
            r"'([\w.]+)'",
            settings_js.split("HUD_TOGGLE_AUTO_SYNC_KEYS = [", 1)[1].split("]", 1)[0],
        )
    )
    assert js_keys, "HUD_TOGGLE_AUTO_SYNC_KEYS is empty or unparseable"
    assert js_keys <= _schema_chip_keys(schema), (
        "auto-synced HUD keys must be chip keys — if one ever becomes a "
        "standalone row, revisit the repaint helper below"
    )

    repaint = re.search(
        r"HUD_TOGGLE_AUTO_SYNC_KEYS\.forEach\((\w+)\)", settings_js
    )
    assert repaint, "master toggle no longer repaints the synced HUD controls"
    helper = settings_js.split(f"function {repaint.group(1)}(", 1)[1]
    assert ".setting-chip[data-key=" in helper.split("\n    }", 1)[0], (
        "the repaint helper must target chip buttons, not setting rows alone"
    )


def test_sprite_visibility_updates_mobile_svg_and_shell_navigation():
    shop_obj = (
        _src / "Ankimon" / "ankimon_items_web" / "shop_obj.py"
    ).read_text(encoding="utf-8")
    nav_css = (
        _src / "Ankimon" / "ankimon_items_web" / "nav-switcher.css"
    ).read_text(encoding="utf-8")
    mobile_html = (
        _src / "Ankimon" / "ankimon_mobile_web" / "mobile.html"
    ).read_text(encoding="utf-8")

    assert "classList.toggle('ankimon-sprites-hidden', !show)" in shop_obj
    assert ".pokeball-icon" in shop_obj
    assert "visibility: hidden !important;" in shop_obj
    assert "body.ankimon-sprites-hidden .nav-trigger .app-logo" in nav_css
    hidden_title_style = nav_css.split(
        "body.ankimon-sprites-hidden .nav-trigger h1 {", 1
    )[1].split("}", 1)[0]
    assert "text-align: left;" in hidden_title_style
    assert "font-size:" not in hidden_title_style
    assert '<svg class="pokeball-icon"' in mobile_html


def test_shop_action_and_purchase_buttons_use_standard_font():
    shop_js = (_src / "Ankimon" / "ankimon_items_web" / "shop.js").read_text(
        encoding="utf-8"
    )
    shop_css = (_src / "Ankimon" / "ankimon_items_web" / "shop.css").read_text(
        encoding="utf-8"
    )

    assert "use.className = 'det-action-btn use'" in shop_js
    assert ".det-action-btn.use.standard-label" not in shop_css
    assert shop_css.count("font-size: 0.92rem;") >= 2
    assert shop_css.count("font-weight: 400;") >= 2
    assert "font-size: inherit;" in shop_css
    assert "font-weight: inherit;" in shop_css


def test_sprite_visibility_hides_web_placeholders_and_mobile_headings():
    shop_css = (_src / "Ankimon" / "ankimon_items_web" / "shop.css").read_text(
        encoding="utf-8"
    )
    ankidex_css = (_src / "Ankimon" / "ankidex" / "ankidex.css").read_text(
        encoding="utf-8"
    )
    mobile_css = (_src / "Ankimon" / "ankimon_mobile_web" / "mobile.css").read_text(
        encoding="utf-8"
    )
    mobile_html = (
        _src / "Ankimon" / "ankimon_mobile_web" / "mobile.html"
    ).read_text(encoding="utf-8")
    history_html = (
        _src / "Ankimon" / "ankimon_mobile_web" / "history.html"
    ).read_text(encoding="utf-8")
    profile_css = (
        _src / "Ankimon" / "ankimon_profile_web" / "profile.css"
    ).read_text(encoding="utf-8")

    assert "body.ankimon-sprites-hidden #detail-placeholder" in shop_css
    assert "body.ankimon-sprites-hidden #briefing-placeholder" in ankidex_css
    assert "mobile-reviews-page" in mobile_html
    assert "mobile-history-page" in history_html
    assert "#state-empty .empty-content > h2" in mobile_css
    assert ".mobile-history-page .top-bar-titles > h2" in mobile_css
    assert "body.ankimon-sprites-hidden .rail-avatar img" in profile_css
    assert "body.ankimon-sprites-hidden .rail-avatar .change-hint { margin-top: 0; }" in profile_css
