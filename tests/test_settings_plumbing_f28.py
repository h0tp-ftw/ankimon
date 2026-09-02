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
    assert "SCREEN_MOBILE" not in allowlist
    assert "SCREEN_HISTORY" not in allowlist
