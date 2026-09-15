"""Characterization tests for the asynchronous startup boot (F32).

Pins the background/UI split of ``startup.py`` and the boot ordering of
``__init__.py`` without Qt:

* ``run_startup_background_checks`` is the aqt-free half: it returns a plain
  results dict and performs NO UI work (no dialogs, no window construction).
* ``run_startup_ui_callbacks`` is the main-thread half: migration dialog,
  sprite downloader, first-enemy stat application, starter window and rate
  prompt all happen here, driven purely by the results dict.
* The legacy ``battle.automatic_catch_special`` toggle folds into F28's seven
  ``battle.auto_catch_*`` keys exactly once (tombstoned afterwards).
* ``__init__.py`` registers every module-scope hook (profile hooks, gated
  review hook, changelog schedule) BEFORE starting the QueryOp boot, keeps
  the changelog check on REAL connectivity, calls ``create_menu_actions``
  with the full base argument list (no exp None placeholders), calls
  ``setup_reviewer_ui`` with base's 3-argument signature, and flips
  ``services.startup_finished`` when the boot completes.
* Re-executing ``__init__.py`` (add-on reload / double boot) must not stack a
  second ``reviewer_did_answer_card`` handler (NR-21): the registration
  record lives on the surviving services registry.

House pattern: mock aqt + the collaborating Ankimon modules in
``sys.modules``, then exec the real module file under its dotted name
(tests/conftest.py provides the package stubs).
"""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_src = Path(__file__).parent.parent / "src"

ENEMY_INFO = (
    "Pikachu",  # name
    25,  # id
    7,  # level
    "static",  # ability
    ["electric"],  # type
    {"hp": 35},  # base_stats
    ["thunder-shock"],  # enemy_attacks
    112,  # base_experience
    "medium",  # growth_rate
    {"hp": 0},  # ev
    {"hp": 1},  # iv
    "male",  # gender
    None,  # battle_status
    {},  # battle_stats
    "Normal",  # tier
    {"hp": 0},  # ev_yield
    False,  # shiny
    "hardy",  # nature
)

AUTO_CATCH_KEYS = (
    "battle.auto_catch_legendary",
    "battle.auto_catch_mythical",
    "battle.auto_catch_ultra",
    "battle.auto_catch_starter",
    "battle.auto_catch_mega",
    "battle.auto_catch_gmax",
    "battle.auto_catch_regional",
)


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class FakeLogger:
    def __init__(self, calls):
        self.calls = calls

    def log(self, level, message):
        self.calls.append(("log", level, message))

    def log_and_showinfo(self, level, message):
        self.calls.append(("log_and_showinfo", level, message))


class FakeSettings:
    def __init__(self, config=None):
        self.config = dict(config or {})
        self.set_calls = []

    def get(self, key, default=None):
        value = self.config.get(key)
        if value is not None:
            return value
        return default

    def set(self, key, value):
        self.config[key] = value
        self.set_calls.append((key, value))


class FakeDB:
    def __init__(self, migrated=True, pokemon_count=3, user_data=None):
        self.migrated = migrated
        self.pokemon_count = pokemon_count
        self.user_data = dict(user_data or {})

    def is_migrated(self):
        return self.migrated

    def get_pokemon_count(self):
        return self.pokemon_count

    def get_user_data(self, key, default=None):
        return self.user_data.get(key, default)


class FakeEnemy:
    def __init__(self):
        self.update_kwargs = None
        self.current_hp = None
        self.hp = None
        self.max_hp = None

    def update_stats(self, **kwargs):
        self.update_kwargs = kwargs

    def calculate_max_hp(self):
        return 42


class FakeTracker:
    def __init__(self, calls):
        self.calls = calls
        self.pokemon_encounter = 99

    def randomize_battle_scene(self):
        self.calls.append(("randomize_battle_scene",))


class FakeBackupManager:
    instances = []

    def __init__(self, logger, settings_obj):
        type(self).instances.append(self)
        self.create_calls = []

    def create_backup(self, manual=False):
        self.create_calls.append(manual)

    def on_anki_close(self):
        pass


class FakeCheckFiles:
    instances = []

    def __init__(self):
        type(self).instances.append(self)
        self.shown = False

    def show(self):
        self.shown = True


class FakeStarterWindow:
    def __init__(self, calls):
        self.calls = calls

    def display_starter_pokemon(self):
        self.calls.append(("display_starter_pokemon",))


# ---------------------------------------------------------------------------
# Part A: the startup.py background/UI split
# ---------------------------------------------------------------------------


@pytest.fixture
def startup_env(monkeypatch, tmp_path):
    """Stub every startup.py collaborator, exec the real module."""
    FakeBackupManager.instances = []
    FakeCheckFiles.instances = []

    calls = []

    def rec(name, result=None):
        def _f(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return _f

    logger = FakeLogger(calls)
    settings = FakeSettings({"misc.developer_mode": False, "misc.language": 9})
    db = FakeDB()
    enemy = FakeEnemy()
    tracker = FakeTracker(calls)
    main_pokemon = SimpleNamespace(level=32)
    starter_window = FakeStarterWindow([])

    translator = SimpleNamespace(translate=lambda key: key)

    env = SimpleNamespace(
        calls=calls,
        logger=logger,
        settings=settings,
        db=db,
        enemy=enemy,
        tracker=tracker,
        main_pokemon=main_pokemon,
        starter_window=starter_window,
        folders_exist=True,
        run_backup_error=None,
    )

    def run_backup():
        calls.append(("run_backup", (), {}))
        if env.run_backup_error is not None:
            raise env.run_backup_error

    monkeypatch.setitem(sys.modules, "aqt", _stub_module("aqt", mw=SimpleNamespace()))
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.resources",
        _stub_module(
            "Ankimon.resources",
            pkmnimgfolder=str(tmp_path),
            mypokemon_path="mypokemon",
            mainpokemon_path="mainpokemon",
            itembag_path="itembag",
            badgebag_path="badgebag",
            team_pokemon_path="team",
            pokemon_history_path="history",
            user_path_credentials="credentials",
            rate_path="rate",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.utils",
        _stub_module(
            "Ankimon.utils",
            check_folders_exist=lambda parent, folder: env.folders_exist,
            get_main_pokemon_data=rec("get_main_pokemon_data"),
            load_collected_pokemon_ids=rec("load_collected_pokemon_ids", {1, 4, 7}),
            count_items_and_rewrite=rec("count_items_and_rewrite"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.encounter_functions",
        _stub_module(
            "Ankimon.functions.encounter_functions",
            generate_random_pokemon=rec("generate_random_pokemon", ENEMY_INFO),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.pokedex_functions",
        _stub_module(
            "Ankimon.functions.pokedex_functions",
            warm_evolution_caches=rec("warm_evolution_caches", 507),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.badges_functions",
        _stub_module(
            "Ankimon.functions.badges_functions",
            get_achieved_badges=lambda: env.badges,
        ),
    )
    env.badges = []
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.rate_addon_functions",
        _stub_module(
            "Ankimon.functions.rate_addon_functions",
            rate_this_addon=rec("rate_this_addon"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.gui_entities",
        _stub_module("Ankimon.gui_entities", CheckFiles=FakeCheckFiles),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.download_sprites",
        _stub_module(
            "Ankimon.pyobj.download_sprites",
            show_agreement_and_download_dialog=rec(
                "show_agreement_and_download_dialog"
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.backup_files",
        _stub_module("Ankimon.pyobj.backup_files", run_backup=run_backup),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.backup_manager",
        _stub_module("Ankimon.pyobj.backup_manager", BackupManager=FakeBackupManager),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.error_handler",
        _stub_module(
            "Ankimon.pyobj.error_handler",
            show_warning_with_traceback=rec("show_warning_with_traceback"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.migration_dialog",
        _stub_module(
            "Ankimon.pyobj.migration_dialog",
            show_migration_dialog_if_needed=rec("show_migration_dialog_if_needed"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.singletons",
        _stub_module(
            "Ankimon.singletons",
            logger=logger,
            translator=translator,
            settings_obj=settings,
            ankimon_tracker_obj=tracker,
            main_pokemon=main_pokemon,
            enemy_pokemon=enemy,
            ankimon_db=db,
            get_starter_window=lambda: starter_window,
        ),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.startup", _src / "Ankimon" / "startup.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.startup", mod)
    spec.loader.exec_module(mod)
    env.mod = mod
    return env


def _called(env, name):
    return [c for c in env.calls if c[0] == name]


def test_background_checks_do_no_ui_work_and_return_contract(startup_env):
    env = startup_env
    results = env.mod.run_startup_background_checks()

    assert set(results) == {
        "backup_error",
        "backup_manager",
        "is_migrated",
        "collected_pokemon_ids",
        "database_complete",
        "enemy_info",
        "needs_starter",
        "needs_rating",
    }
    assert results["backup_error"] is None
    assert results["is_migrated"] is True
    assert results["collected_pokemon_ids"] == {1, 4, 7}
    assert results["database_complete"] is True
    assert results["enemy_info"] == ENEMY_INFO
    assert results["needs_starter"] is False  # pokemon_count > 0
    assert results["needs_rating"] is False  # no badges

    # The aqt-free half must not have touched any UI collaborator.
    assert FakeCheckFiles.instances == []
    assert _called(env, "show_agreement_and_download_dialog") == []
    assert _called(env, "show_migration_dialog_if_needed") == []
    assert _called(env, "rate_this_addon") == []
    assert env.starter_window.calls == []
    # ...but the background work did run.
    assert _called(env, "run_backup")
    assert _called(env, "generate_random_pokemon")
    assert _called(env, "count_items_and_rewrite")
    assert _called(env, "warm_evolution_caches")


def test_background_checks_warm_the_evolution_table(startup_env):
    """``pokemon_evolution.csv`` must be parsed HERE, not by the first level-up.

    Every consumer of the evolution rows (the gender gate, the friendship and
    level-up lookups) runs inside ``on_review_card``, and the lazy loaders let
    whichever caller arrives first pay the ~500-row parse — synchronous disk
    I/O mid-review, which AGENTS.md forbids. Warming on the boot thread is what
    makes that impossible for the session: reviews are gated on
    ``services.startup_finished``, which flips only once this half has
    returned, so no review can precede the warm.
    """
    env = startup_env
    env.mod.run_startup_background_checks()

    assert len(_called(env, "warm_evolution_caches")) == 1


def test_evolution_table_is_warmed_even_when_assets_are_missing(startup_env):
    """The evolution CSV ships inside the add-on, so it is warmable whether or
    not the player's sprite folders are complete. Hanging the warm off
    ``database_complete`` (as the first-enemy step is) would leave the table
    cold for exactly the players whose next boot step is a download dialog."""
    env = startup_env
    env.folders_exist = False

    results = env.mod.run_startup_background_checks()

    assert results["database_complete"] is False
    assert len(_called(env, "warm_evolution_caches")) == 1


def test_a_failing_warm_cannot_fail_the_boot(startup_env, monkeypatch):
    """The warm rides on a QueryOp with no recovery: a raise here skips
    ``on_startup_complete`` entirely, so ``services.startup_finished`` stays
    False and every answered card is silently dropped for the session. Failing
    to pre-parse a CSV must never cost the player the whole add-on."""
    env = startup_env

    def boom():
        raise OSError("data_files unreadable")

    monkeypatch.setattr(env.mod, "warm_evolution_caches", boom)

    results = env.mod.run_startup_background_checks()

    # The boot completed and the rest of the background work still ran.
    assert results["database_complete"] is True
    assert _called(env, "count_items_and_rewrite")
    assert any(
        call[0] == "log" and call[1] == "error" and "data_files unreadable" in call[2]
        for call in env.calls
    )


def test_background_checks_flag_starter_and_rating(startup_env):
    env = startup_env
    env.db.pokemon_count = 0
    env.badges = ["boulder", "cascade"]

    results = env.mod.run_startup_background_checks()
    assert results["needs_starter"] is True
    assert results["needs_rating"] is True

    env.mod.run_startup_ui_callbacks(results)
    assert env.starter_window.calls == [("display_starter_pokemon",)]
    assert len(_called(env, "rate_this_addon")) == 1


def test_rating_not_needed_when_already_rated(startup_env):
    env = startup_env
    env.badges = ["boulder", "cascade"]
    env.db.user_data["rate_this"] = True

    results = env.mod.run_startup_background_checks()
    assert results["needs_rating"] is False


def test_ui_callbacks_apply_first_enemy_and_reset_tracker(startup_env):
    env = startup_env
    results = env.mod.run_startup_background_checks()
    returned = env.mod.run_startup_ui_callbacks(results)

    assert returned is True
    assert env.enemy.update_kwargs["name"] == "Pikachu"
    assert env.enemy.update_kwargs["id"] == 25
    assert env.enemy.update_kwargs["attacks"] == ["thunder-shock"]
    assert env.enemy.update_kwargs["nature"] == "hardy"
    assert env.enemy.current_hp == env.enemy.hp == env.enemy.max_hp == 42
    assert _called(env, "randomize_battle_scene")
    assert env.tracker.pokemon_encounter == 0


def test_ui_callbacks_show_downloader_and_keep_checker_alive(startup_env):
    env = startup_env
    env.folders_exist = False

    results = env.mod.run_startup_background_checks()
    assert results["database_complete"] is False
    assert results["enemy_info"] is None

    returned = env.mod.run_startup_ui_callbacks(results)
    assert returned is False

    dialog_calls = _called(env, "show_agreement_and_download_dialog")
    assert len(dialog_calls) == 1
    assert dialog_calls[0][2] == {"force_download": True}
    assert len(FakeCheckFiles.instances) == 1
    assert FakeCheckFiles.instances[0].shown is True
    # The dialog is anchored on the module so it cannot be garbage-collected.
    assert env.mod._file_check_dialog is FakeCheckFiles.instances[0]
    # No enemy was applied on the incomplete path.
    assert env.enemy.update_kwargs is None


def test_backup_error_is_reported_on_the_ui_side(startup_env):
    env = startup_env
    boom = RuntimeError("backup boom")
    env.run_backup_error = boom

    results = env.mod.run_startup_background_checks()
    assert results["backup_error"] is boom
    # Not reported from the background thread...
    assert _called(env, "show_warning_with_traceback") == []

    env.mod.run_startup_ui_callbacks(results)
    warn_calls = _called(env, "show_warning_with_traceback")
    assert len(warn_calls) == 1
    assert warn_calls[0][2]["exception"] is boom


def test_migration_dialog_shown_only_when_not_migrated(startup_env):
    env = startup_env
    env.db.migrated = False

    results = env.mod.run_startup_background_checks()
    assert results["is_migrated"] is False
    env.mod.run_startup_ui_callbacks(results)
    assert len(_called(env, "show_migration_dialog_if_needed")) == 1


def test_dev_mode_auto_backup_uses_passed_manager(startup_env):
    env = startup_env
    env.settings.config["misc.developer_mode"] = True

    manager = FakeBackupManager(env.logger, env.settings)
    results = env.mod.run_startup_background_checks(manager)

    assert results["backup_manager"] is manager
    assert manager.create_calls == [False]
    # No second manager was constructed inside the background half.
    assert FakeBackupManager.instances == [manager]


def test_hot_reload_skips_both_background_backups(startup_env, monkeypatch):
    env = startup_env
    env.settings.config["misc.developer_mode"] = True
    services = sys.modules["Ankimon.services"].services
    monkeypatch.setattr(services, "_is_reloading", True, raising=False)

    manager = FakeBackupManager(env.logger, env.settings)
    results = env.mod.run_startup_background_checks(manager)

    assert results["backup_manager"] is manager
    assert _called(env, "run_backup") == []
    assert manager.create_calls == []
    assert (
        "log",
        "info",
        "Skipping background backups during hot-reload.",
    ) in env.calls
    # The remainder of startup still runs normally.
    assert _called(env, "generate_random_pokemon")
    assert _called(env, "count_items_and_rewrite")


def test_auto_catch_migration_folds_legacy_key_once(startup_env):
    env = startup_env
    env.settings.config["battle.automatic_catch_special"] = False
    # F28's load_config seeds the new keys with defaults before startup runs.
    for key in AUTO_CATCH_KEYS:
        env.settings.config[key] = True

    env.mod.run_startup_background_checks()

    for key in AUTO_CATCH_KEYS:
        assert env.settings.config[key] is False, key
    # Tombstoned: the legacy key is neutralized...
    assert env.settings.config["battle.automatic_catch_special"] is None

    # ...so a second boot (where the DB round-trips None as "None") is a no-op.
    env.settings.config["battle.automatic_catch_special"] = "None"
    env.settings.set_calls.clear()
    env.mod.run_startup_background_checks()
    assert env.settings.set_calls == []


def test_auto_catch_migration_noop_without_legacy_key(startup_env):
    env = startup_env
    for key in AUTO_CATCH_KEYS:
        env.settings.config[key] = True

    env.mod.run_startup_background_checks()
    assert env.settings.set_calls == []
    for key in AUTO_CATCH_KEYS:
        assert env.settings.config[key] is True


def test_auto_catch_migration_coerces_string_toggle(startup_env):
    # A legacy value that survives the config DB layer's str() fallback comes
    # back as the string "True"/"False" — bool("False") is truthy, so the
    # migration must normalize the string form, not fold it verbatim.
    env = startup_env
    for legacy, expected in (
        ("False", False),
        ("True", True),
        ("1", True),
        ("0", False),
    ):
        env.settings.config.clear()
        env.settings.config["battle.automatic_catch_special"] = legacy
        for key in AUTO_CATCH_KEYS:
            env.settings.config[key] = not expected

        env.mod.run_startup_background_checks()

        for key in AUTO_CATCH_KEYS:
            assert env.settings.config[key] is expected, (legacy, key)
        # Every migrated key is a real bool, never the raw string.
        for key in AUTO_CATCH_KEYS:
            assert isinstance(env.settings.config[key], bool), (legacy, key)
        assert env.settings.config["battle.automatic_catch_special"] is None


# ---------------------------------------------------------------------------
# Part B: __init__.py boot ordering + reload-safe review hook (NR-21)
# ---------------------------------------------------------------------------


class SyncQueryOp:
    """The harness QueryOp contract: op off-thread, success on the GUI thread
    — here synchronous, so a test observes the full boot deterministically."""

    instances = []

    def __init__(self, *, parent=None, op=None, success=None):
        type(self).instances.append(self)
        self._op = op
        self._success = success
        self._failure = None
        self.without_collection_called = False

    def failure(self, cb):
        # Model aqt.operations.QueryOp.failure: on-op-exception callback.
        self._failure = cb
        return self

    def without_collection(self):
        self.without_collection_called = True
        return self

    def run_in_background(self):
        try:
            result = self._op(None) if self._op else None
        except Exception as exc:
            if self._failure:
                self._failure(exc)
            return None
        if self._success:
            self._success(result)
        return result


def _fresh_services(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.services", mod)
    spec.loader.exec_module(mod)
    return mod.services


@pytest.fixture
def boot_env(monkeypatch):
    """Stub every __init__.py collaborator; exec_init() runs the real boot."""
    SyncQueryOp.instances = []
    calls = []

    def rec(name, result=None):
        def _f(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return _f

    FakeBackupManager.instances = []

    services = _fresh_services(monkeypatch)

    settings = FakeSettings(
        {
            "misc.YouShallNotPass_Ankimon_News": False,
            "misc.ssh": True,
            "controls.key_for_opening_closing_ankimon": "Ctrl+Shift+P",
            "controls.catch_key": "6",
            "controls.defeat_key": "5",
            "controls.pokemon_buttons": True,
        }
    )
    logger = FakeLogger(calls)
    tracker = FakeTracker(calls)

    hooks = SimpleNamespace(reviewer_did_answer_card=[])
    mw = SimpleNamespace(
        addonManager=SimpleNamespace(
            setWebExports=rec("setWebExports"),
            addonFromModule=lambda name: "ankimon",
        )
    )
    aqt_stub = _stub_module(
        "aqt",
        mw=mw,
        gui_hooks=hooks,
        reviewer=SimpleNamespace(Reviewer=type("Reviewer", (), {})),
    )
    monkeypatch.setitem(sys.modules, "aqt", aqt_stub)

    webview_hook = []
    monkeypatch.setitem(
        sys.modules,
        "aqt.gui_hooks",
        _stub_module("aqt.gui_hooks", webview_will_set_content=webview_hook),
    )
    monkeypatch.setitem(
        sys.modules,
        "aqt.webview",
        _stub_module("aqt.webview", WebContent=type("WebContent", (), {})),
    )
    monkeypatch.setitem(
        sys.modules,
        "aqt.operations",
        _stub_module("aqt.operations", QueryOp=SyncQueryOp),
    )

    background_results = {
        "backup_error": None,
        "backup_manager": None,
        "is_migrated": True,
        "collected_pokemon_ids": {10, 20},
        "database_complete": True,
        "enemy_info": ENEMY_INFO,
        "needs_starter": False,
        "needs_rating": False,
    }

    def run_startup_background_checks(backup_manager=None):
        calls.append(("run_startup_background_checks", (backup_manager,), {}))
        results = dict(background_results)
        results["backup_manager"] = backup_manager
        return results

    def run_startup_ui_callbacks(results):
        calls.append(("run_startup_ui_callbacks", (results,), {}))
        return results["database_complete"]

    singletons = _stub_module(
        "Ankimon.singletons",
        settings_obj=settings,
        logger=logger,
        translator=SimpleNamespace(translate=lambda key: key),
        ankimon_tracker_obj=tracker,
        shop_manager=SimpleNamespace(),
        trainer_card=SimpleNamespace(),
        settings_window=object(),
        test_window=object(),
        achievement_bag=object(),
        ankimon_tracker_window=object(),
        pokedex_window=object(),
        eff_chart=object(),
        gen_id_chart=object(),
        license=object(),
        credits=object(),
        item_window=object(),
        version_dialog=object(),
        pokemon_pc=object(),
        nature_chart=object(),
    )

    stubs = {
        "Ankimon.resources": _stub_module(
            "Ankimon.resources",
            ensure_ankimon_infrastructure=rec("ensure_ankimon_infrastructure"),
            user_path="user_path",
            addon_dir="addon_dir",
        ),
        "Ankimon.singletons": singletons,
        "Ankimon.functions.url_functions": _stub_module(
            "Ankimon.functions.url_functions",
            open_team_builder=rec("open_team_builder"),
            rate_addon_url=rec("rate_addon_url"),
            report_bug=rec("report_bug"),
            join_discord_url=rec("join_discord_url"),
            open_leaderboard_url=rec("open_leaderboard_url"),
        ),
        "Ankimon.functions.pokemon_showdown_functions": _stub_module(
            "Ankimon.functions.pokemon_showdown_functions",
            export_to_pkmn_showdown=rec("export_to_pkmn_showdown"),
            export_all_pkmn_showdown=rec("export_all_pkmn_showdown"),
            flex_pokemon_collection=rec("flex_pokemon_collection"),
        ),
        "Ankimon.utils": _stub_module(
            "Ankimon.utils",
            test_online_connectivity=rec("test_online_connectivity", True),
            is_dev_mode=rec("is_dev_mode", False),
        ),
        "PyQt6.QtGui": _stub_module(
            "PyQt6.QtGui",
            QKeySequence=lambda *args: None,
            QShortcut=lambda *args, **kwargs: SimpleNamespace(
                activated=SimpleNamespace(connect=rec("QShortcut.activated.connect")),
                setEnabled=rec("QShortcut.setEnabled"),
            ),
        ),
        "Ankimon.menu_buttons": _stub_module(
            "Ankimon.menu_buttons",
            create_menu_actions=rec("create_menu_actions"),
        ),
        "Ankimon.hooks": _stub_module("Ankimon.hooks", setupHooks=rec("setupHooks")),
        "Ankimon.pyobj.error_handler": _stub_module(
            "Ankimon.pyobj.error_handler",
            show_warning_with_traceback=rec("show_warning_with_traceback"),
        ),
        "Ankimon.pyobj.backup_manager": _stub_module(
            "Ankimon.pyobj.backup_manager", BackupManager=FakeBackupManager
        ),
        "Ankimon.events": _stub_module(
            "Ankimon.events", events=SimpleNamespace(emit=rec("events.emit"))
        ),
        "Ankimon.gui_classes.overview_team": _stub_module(
            "Ankimon.gui_classes.overview_team",
            register_overview_hooks=rec("register_overview_hooks"),
        ),
        "Ankimon.card_hooks": _stub_module(
            "Ankimon.card_hooks",
            register_card_hooks=rec("register_card_hooks"),
        ),
        "Ankimon.changelog": _stub_module(
            "Ankimon.changelog",
            check_and_show_changelog=rec("check_and_show_changelog"),
            open_help_window=rec("open_help_window"),
            schedule_branch_update_check=rec("schedule_branch_update_check"),
        ),
        "Ankimon.hook_registry": _stub_module(
            "Ankimon.hook_registry",
            CatchPokemonHook=rec("CatchPokemonHook"),
            DefeatPokemonHook=rec("DefeatPokemonHook"),
            add_catch_pokemon_hook=rec("add_catch_pokemon_hook"),
            add_defeat_pokemon_hook=rec("add_defeat_pokemon_hook"),
        ),
        "Ankimon.profile_hooks": _stub_module(
            "Ankimon.profile_hooks",
            register_profile_hooks=rec("register_profile_hooks"),
        ),
        "Ankimon.reviewer_ui": _stub_module(
            "Ankimon.reviewer_ui",
            setup_reviewer_ui=rec("setup_reviewer_ui"),
            set_collected_ids=rec("set_collected_ids"),
        ),
        "Ankimon.discord_integration": _stub_module(
            "Ankimon.discord_integration",
            setup_discord_hooks=rec("setup_discord_hooks"),
        ),
        "Ankimon.startup": _stub_module(
            "Ankimon.startup",
            run_startup_background_checks=run_startup_background_checks,
            run_startup_ui_callbacks=run_startup_ui_callbacks,
        ),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # `from .battle_loop import on_review_card` needs a real-looking module.
    def on_review_card(*args, **kwargs):
        calls.append(("on_review_card", args, kwargs))

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.battle_loop",
        _stub_module(
            "Ankimon.battle_loop",
            on_review_card=on_review_card,
            init_battle_state=rec("init_battle_state"),
        ),
    )

    # `from .gui_classes.overview_team import register_overview_hooks` resolves
    # the submodule (stubbed above) via this parent package.
    gui_classes_pkg = _stub_module("Ankimon.gui_classes")
    gui_classes_pkg.overview_team = stubs["Ankimon.gui_classes.overview_team"]
    monkeypatch.setitem(sys.modules, "Ankimon.gui_classes", gui_classes_pkg)

    def exec_init():
        sys.modules.pop("Ankimon", None)
        spec = importlib.util.spec_from_file_location(
            "Ankimon",
            _src / "Ankimon" / "__init__.py",
            submodule_search_locations=[str(_src / "Ankimon")],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["Ankimon"] = mod
        spec.loader.exec_module(mod)
        return mod

    return SimpleNamespace(
        calls=calls,
        services=services,
        settings=settings,
        hooks=hooks,
        singletons=singletons,
        exec_init=exec_init,
    )


def _names(env):
    return [c[0] for c in env.calls]


def _first(env, name):
    for c in env.calls:
        if c[0] == name:
            return c
    raise AssertionError(f"{name} was never called")


def test_boot_ordering_background_then_ui_then_menu(boot_env):
    boot_env.exec_init()
    names = _names(boot_env)

    # Module-scope registrations happen BEFORE the async boot starts, so a
    # profileLoaded/profile_did_open fired mid-boot can never be missed.
    assert names.index("register_profile_hooks") < names.index(
        "run_startup_background_checks"
    )
    assert names.index("register_card_hooks") < names.index(
        "run_startup_background_checks"
    )
    # Background half -> UI half -> menu, in order.
    assert (
        names.index("run_startup_background_checks")
        < names.index("run_startup_ui_callbacks")
        < names.index("create_menu_actions")
    )
    # The QueryOp ran collection-free.
    assert len(SyncQueryOp.instances) == 1
    assert SyncQueryOp.instances[0].without_collection_called is True
    assert boot_env.services._startup_in_progress is False
    assert boot_env.services._is_reloading is False


def test_startup_failure_clears_reload_lifecycle_flags(boot_env):
    observed_startup_flags = []

    def fail_background_startup(backup_manager=None):
        observed_startup_flags.append(boot_env.services._startup_in_progress)
        raise RuntimeError("startup boom")

    sys.modules[
        "Ankimon.startup"
    ].run_startup_background_checks = fail_background_startup
    boot_env.services._is_reloading = True

    boot_env.exec_init()

    assert observed_startup_flags == [True]
    assert boot_env.services._startup_in_progress is False
    assert boot_env.services._is_reloading is False


def test_success_callback_failure_clears_reload_lifecycle_flags(boot_env):
    """QueryOp routes only *op* exceptions to .failure(); a raising success
    callback propagates instead, so the flag reset has to be a finally."""

    observed = []

    def fail_ui_startup(results):
        observed.append(
            (
                boot_env.services._startup_in_progress,
                boot_env.services._is_reloading,
            )
        )
        raise RuntimeError("qt half boom")

    sys.modules["Ankimon.startup"].run_startup_ui_callbacks = fail_ui_startup
    boot_env.services._is_reloading = True

    with pytest.raises(RuntimeError, match="qt half boom"):
        boot_env.exec_init()

    # Pin the *transition*: both flags were still set when the callback blew
    # up, so the final False below can only come from the finally.
    assert observed == [(True, True)]
    # Left set, restart_ankimon() would block on _startup_in_progress until its
    # timeout on every later Ctrl+Shift+R, and backups would stay suppressed.
    assert boot_env.services._startup_in_progress is False
    assert boot_env.services._is_reloading is False


def test_unschedulable_queryop_clears_reload_lifecycle_flags(boot_env):
    """If run_in_background() raises, neither callback ever runs."""
    real_run_in_background = SyncQueryOp.run_in_background
    observed = []

    def fail_to_schedule(self):
        observed.append(
            (
                boot_env.services._startup_in_progress,
                boot_env.services._is_reloading,
            )
        )
        raise RuntimeError("taskman gone")

    SyncQueryOp.run_in_background = fail_to_schedule
    boot_env.services._is_reloading = True
    try:
        with pytest.raises(RuntimeError, match="taskman gone"):
            boot_env.exec_init()
    finally:
        SyncQueryOp.run_in_background = real_run_in_background

    assert observed == [(True, True)]
    assert boot_env.services._startup_in_progress is False
    assert boot_env.services._is_reloading is False


def test_services_declares_and_resets_the_hot_reload_flags(boot_env):
    """The registry is what survives _purge_addon_modules, so the hot-reload
    flags have to live there and come back false on a profile-level reset."""
    services = boot_env.services
    for attr in ("_startup_in_progress", "_is_reloading", "_reload_in_progress"):
        assert getattr(services, attr) is False
        setattr(services, attr, True)

    services.reset()

    for attr in ("_startup_in_progress", "_is_reloading", "_reload_in_progress"):
        assert getattr(services, attr) is False


def test_changelog_keeps_real_connectivity(boot_env):
    """exp stubbed online_connectivity=False and dropped the changelog call;
    the port must keep both on the REAL connectivity result."""
    boot_env.exec_init()

    changelog = _first(boot_env, "check_and_show_changelog")
    # (online_connectivity, ssh, no_more_news) — connectivity is the real
    # test_online_connectivity() result (stubbed True), NOT a False stub.
    assert changelog[1] == (True, True, False)

    branch_check = _first(boot_env, "schedule_branch_update_check")
    assert branch_check[1] == (True, True)


def test_menu_gets_base_call_shape_no_none_placeholders(boot_env):
    mod = boot_env.exec_init()

    menu = _first(boot_env, "create_menu_actions")
    args = menu[1]
    assert len(args) == 31
    # exp passed 11 None placeholders; the port passes base's real objects.
    assert not any(arg is None for arg in args)
    assert args[0] is True  # database_complete
    assert args[1] is True  # online_connectivity (real result)
    assert args[2] is boot_env.singletons.item_window
    assert args[3] is boot_env.singletons.test_window
    assert args[-1] is mod.backup_manager


def test_reviewer_ui_called_with_base_three_arg_signature(boot_env):
    boot_env.exec_init()

    setup = _first(boot_env, "setup_reviewer_ui")
    assert setup[1] == ("6", "5", True)
    assert setup[2] == {}


def test_collected_ids_shared_by_identity_across_consumers(boot_env):
    mod = boot_env.exec_init()

    profile = _first(boot_env, "register_profile_hooks")
    battle = _first(boot_env, "init_battle_state")
    reviewer = _first(boot_env, "set_collected_ids")

    # One live set object everywhere...
    assert profile[1][6] is mod.collected_pokemon_ids
    assert battle[1][0] is mod.collected_pokemon_ids
    assert reviewer[1][0] is mod.collected_pokemon_ids
    # ...filled in place by the async boot's results.
    assert mod.collected_pokemon_ids == {10, 20}

    # The background half received the module's own BackupManager.
    background = _first(boot_env, "run_startup_background_checks")
    assert background[1][0] is mod.backup_manager


def test_startup_finished_flag_gates_the_review_hook(boot_env):
    boot_env.exec_init()

    # The synchronous QueryOp completed the boot during exec.
    assert getattr(boot_env.services, "startup_finished") is True

    handlers = boot_env.hooks.reviewer_did_answer_card
    assert len(handlers) == 1
    handler = handlers[0]

    # Gate closed: the review is dropped before the boot finishes.
    setattr(boot_env.services, "startup_finished", False)
    handler("reviewer", "card", 3)
    assert [c for c in boot_env.calls if c[0] == "on_review_card"] == []

    # Gate open: the review reaches the battle loop.
    setattr(boot_env.services, "startup_finished", True)
    handler("reviewer", "card", 3)
    forwarded = [c for c in boot_env.calls if c[0] == "on_review_card"]
    assert len(forwarded) == 1
    assert forwarded[0][1] == ("reviewer", "card", 3)


def test_double_boot_does_not_stack_review_handlers(boot_env):
    """NR-21: a re-execution of __init__ (add-on reload) must swap the
    reviewer_did_answer_card handler, not append a second one."""
    boot_env.exec_init()
    assert len(boot_env.hooks.reviewer_did_answer_card) == 1
    first_handler = boot_env.hooks.reviewer_did_answer_card[0]

    mod2 = boot_env.exec_init()

    handlers = boot_env.hooks.reviewer_did_answer_card
    assert len(handlers) == 1, "reload must not stack review handlers"
    assert handlers[0] is not first_handler
    assert handlers[0] is mod2._on_review_card_gated
