"""Dynamic leaderboard sync contract (``TrainerCard.sync_leaderboard``).

The leaderboard used to be published exactly once, from ``TrainerCard.__init__``
— so a player whose credentials landed after boot, or who levelled up during the
session, stayed frozen at their startup snapshot. The sync is now also driven
from ``singletons.notify_stats_changed()``, i.e. from every gameplay write
chokepoint (XP gain, catch, cash reward).

That makes the method hot, so this file pins the four properties that keep it
safe to call on a review path:

* **Opt-in first** — ``misc.leaderboard`` is read before any database work, so
  the (default) users who never enable the leaderboard pay nothing.
* **Rate limited** — at most one push per ``LEADERBOARD_SYNC_MIN_INTERVAL``,
  with ``force=True`` for the startup/account-switch push.
* **Fresh values** — the payload is rebuilt from settings + database on every
  push, never from the cached ``self.cash`` / ``self.league`` attributes, which
  go stale mid-session (shop purchases don't write back to ``self.cash``, and
  ``self.league`` is only recomputed by ``refresh()``).
* **Contained** — never raises and never opens a dialog, whatever the database
  or the leaderboard module does.

House pattern: stub the sibling Ankimon modules in ``sys.modules`` via
``monkeypatch.setitem`` (so nothing leaks into the wider Tier-1 suite), then exec
the real ``pyobj/trainer_card.py`` against them.
"""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"
_TRAINER_CARD_PATH = _src / "Ankimon" / "pyobj" / "trainer_card.py"


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class FakeSettings:
    """Minimal stand-in for ``pyobj.settings.Settings`` (same get/set contract)."""

    def __init__(self, values):
        self.config = dict(values)

    def get(self, key, default=None):
        value = self.config.get(key)
        if value is not None:
            return value
        return default

    def set(self, key, value):
        self.config[key] = value


class FakeDB:
    """Counts every query so a test can assert 'the gate ran before the DB did'."""

    def __init__(self):
        self.calls = []
        self.pokedex_count = 7
        self.pokemon_count = 12
        self.shiny_count = 2
        self.highest_level = 42
        self.raise_on_execute = False
        self.raise_on_highest_level_query = False

    def execute(self, sql, *args):
        self.calls.append(sql)
        if self.raise_on_execute:
            raise RuntimeError("no such table: captured_pokemon")
        if "ORDER BY level DESC" in sql:
            if self.raise_on_highest_level_query:
                raise RuntimeError("database is locked")
            return SimpleNamespace(fetchone=lambda: {"level": self.highest_level})
        return SimpleNamespace(fetchone=lambda: [self.pokedex_count])

    def get_pokemon_count(self):
        self.calls.append("get_pokemon_count")
        return self.pokemon_count

    def get_shiny_count(self):
        self.calls.append("get_shiny_count")
        return self.shiny_count

    def get_team(self):
        self.calls.append("get_team")
        return []


class FakeClock:
    """Hand-cranked replacement for ``time.monotonic`` (no sleeping in tests)."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def env(monkeypatch):
    """Real ``trainer_card`` module against stubbed siblings + a fake clock."""
    db = FakeDB()
    settings = FakeSettings(
        {
            "misc.leaderboard": True,
            "trainer.level": 5,
            "trainer.xp": 0,
            "trainer.total_xp": 0,
            "trainer.cash": 100,
            "trainer.sprite": "ash",
            "trainer.name": "Nuz",
        }
    )
    notifications = []
    services = SimpleNamespace(
        db=db,
        settings=settings,
        # In production this port is QtPresenter, whose notify() is a modal
        # showInfo(); record every call so a test can prove the sync path
        # never reaches it.
        ui=SimpleNamespace(notify=lambda level, message: notifications.append((level, message))),
    )

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.resources",
        _stub_module(
            "Ankimon.resources",
            trainer_sprites_path=Path("/tmp/sprites"),
            mypokemon_path=Path("/tmp/mypokemon.json"),
            team_pokemon_path=Path("/tmp/team.json"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.trainer_functions",
        _stub_module(
            "Ankimon.functions.trainer_functions",
            # Echo the inputs back so a test can read which level / highest level
            # the payload was actually computed from.
            find_trainer_rank=lambda highest_level, trainer_level: f"rank({highest_level},{trainer_level})",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.badges_functions",
        _stub_module("Ankimon.functions.badges_functions", get_achieved_badges=lambda: []),
    )
    monkeypatch.setitem(
        sys.modules, "Ankimon.services", _stub_module("Ankimon.services", services=services)
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.events",
        _stub_module("Ankimon.events", events=SimpleNamespace(emit=lambda *a, **k: None)),
    )

    pushes = []

    def sync_data_to_leaderboard(data):
        pushes.append(data)

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.ankimon_leaderboard",
        _stub_module(
            "Ankimon.pyobj.ankimon_leaderboard",
            sync_data_to_leaderboard=sync_data_to_leaderboard,
        ),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.trainer_card", _TRAINER_CARD_PATH
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    clock = FakeClock()
    # Replace the module's own `time` binding rather than patching the real
    # time module, which would leak into every other test in the session.
    # raising=False so the fixture still builds against a tree without the rate
    # limit — that is what lets these tests fail loudly (rather than error out)
    # if the hardening is ever reverted.
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=clock.monotonic), raising=False
    )
    # The rate-limit stamp is a module global and survives module reuse; start
    # every test from a known point on the fake clock.
    monkeypatch.setattr(module, "_last_leaderboard_sync", 0.0, raising=False)

    return SimpleNamespace(
        module=module,
        db=db,
        settings=settings,
        services=services,
        pushes=pushes,
        clock=clock,
        notifications=notifications,
    )


def _interval(env):
    """The rate-limit window (0 on a tree that has no rate limit at all)."""
    return getattr(env.module, "LEADERBOARD_SYNC_MIN_INTERVAL", 0.0)


def _make_card(env, **overrides):
    kwargs = dict(
        logger=SimpleNamespace(log_and_showinfo=lambda *a: None, log=lambda *a: None),
        main_pokemon=SimpleNamespace(name="Pikachu"),
        settings_obj=env.settings,
        trainer_name="Nuz",
        trainer_id="1234",
        team="",
    )
    kwargs.update(overrides)
    return env.module.TrainerCard(**kwargs)


# --------------------------------------------------------------------------
# Opt-in gate
# --------------------------------------------------------------------------


def test_leaderboard_disabled_does_no_database_work_and_does_not_push(env):
    env.settings.set("misc.leaderboard", False)
    card = _make_card(env)

    env.db.calls.clear()
    assert card.sync_leaderboard() is False
    assert env.db.calls == [], "the opt-in must be checked before any query runs"
    assert env.pushes == []


def test_startup_sync_pushes_when_enabled(env):
    card = _make_card(env)

    # __init__ forces a sync, so the startup push has already happened.
    assert len(env.pushes) == 1
    assert card is not None


def test_missing_settings_object_is_a_no_op(env):
    card = _make_card(env)
    card.settings_obj = None
    env.pushes.clear()

    assert card.sync_leaderboard(force=True) is False
    assert env.pushes == []


def test_missing_database_is_a_no_op(env):
    card = _make_card(env)
    env.services.db = None
    env.pushes.clear()

    assert card.sync_leaderboard(force=True) is False
    assert env.pushes == []


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_startup_sync_consumes_the_rate_limit_window(env):
    card = _make_card(env)
    assert len(env.pushes) == 1

    # A stat change moments after boot has nothing new to say.
    assert card.sync_leaderboard() is False
    assert len(env.pushes) == 1


def test_repeat_syncs_inside_the_interval_are_dropped(env):
    card = _make_card(env)
    env.clock.advance(_interval(env))
    env.pushes.clear()

    assert card.sync_leaderboard() is True
    for _ in range(50):
        assert card.sync_leaderboard() is False

    assert len(env.pushes) == 1, "notify_stats_changed must not open a socket per review"


def test_sync_resumes_after_the_interval_elapses(env):
    card = _make_card(env)
    env.clock.advance(_interval(env))
    env.pushes.clear()

    assert card.sync_leaderboard() is True
    env.clock.advance(_interval(env) + 0.1)
    assert card.sync_leaderboard() is True

    assert len(env.pushes) == 2


def test_force_bypasses_the_rate_limit(env):
    card = _make_card(env)
    env.clock.advance(_interval(env))
    env.pushes.clear()

    assert card.sync_leaderboard() is True
    assert card.sync_leaderboard() is False
    assert card.sync_leaderboard(force=True) is True

    assert len(env.pushes) == 2


def test_a_failed_push_still_consumes_the_rate_limit_window(env):
    card = _make_card(env)
    env.clock.advance(_interval(env))
    env.pushes.clear()
    env.db.raise_on_execute = True

    assert card.sync_leaderboard() is False
    env.db.calls.clear()

    # Backing off matters most when the payload is what's broken: retrying on
    # every stat change would re-run the queries forever.
    assert card.sync_leaderboard() is False
    assert env.db.calls == []


# --------------------------------------------------------------------------
# Freshness — the actual bug this sync exists to fix
# --------------------------------------------------------------------------


def test_payload_uses_live_settings_not_the_cached_attributes(env):
    card = _make_card(env)
    env.pushes.clear()

    # Simulate a session's worth of drift: the shop spent cash without writing
    # back to trainer_card.cash, and a level-up never recomputed the league.
    env.settings.set("trainer.cash", 25)
    env.settings.set("trainer.level", 30)
    env.settings.set("trainer.sprite", "red")
    env.settings.set("trainer.name", "Renamed")
    env.db.highest_level = 60
    assert card.cash == 100 and card.level == 5, "cached attributes are still stale"

    assert card.sync_leaderboard(force=True) is True
    payload = env.pushes[-1]

    assert payload["cash"] == 25
    assert payload["level"] == 30
    assert payload["trainerLevel"] == 30
    assert payload["highestLevel"] == 60
    assert payload["trainerRank"] == "rank(60,30)"
    assert payload["trainerSprite"] == "red.png"
    assert payload["trainerName"] == "Renamed"


def test_payload_keeps_the_server_field_contract(env):
    _make_card(env)
    payload = env.pushes[-1]

    assert set(payload) == {
        "trainerRank",
        "trainerName",
        "level",
        "pokedex",
        "caughtPokemon",
        "trainerLevel",
        "highestLevel",
        "shinies",
        "cash",
        "trainerSprite",
    }
    assert payload["pokedex"] == env.db.pokedex_count
    assert payload["caughtPokemon"] == env.db.pokemon_count
    # shinies has always been sent as a string; keep it that way.
    assert payload["shinies"] == str(env.db.shiny_count)
    assert isinstance(payload["cash"], int)


def test_level_is_floored_at_one(env):
    env.settings.set("trainer.level", 0)
    _make_card(env)
    payload = env.pushes[-1]

    assert payload["level"] == 1
    assert payload["trainerLevel"] == 0


# --------------------------------------------------------------------------
# refresh() — the account switch / rename / sprite-change path
# --------------------------------------------------------------------------


def test_refresh_republishes_the_new_values(env):
    card = _make_card(env)
    env.clock.advance(_interval(env))
    env.pushes.clear()

    # What swap_ankimon_account and the profile screen's rename/sprite handlers
    # do: write settings, then refresh() the card.
    env.settings.set("trainer.name", "SecondAccount")
    env.settings.set("trainer.sprite", "blue")
    env.settings.set("trainer.cash", 9999)
    card.refresh()

    assert len(env.pushes) == 1, "refresh() must republish the switched-to state"
    payload = env.pushes[-1]
    assert payload["trainerName"] == "SecondAccount"
    assert payload["trainerSprite"] == "blue.png"
    assert payload["cash"] == 9999


def test_refresh_respects_the_rate_limit(env):
    card = _make_card(env)
    env.pushes.clear()

    # A refresh moments after the startup push is still throttled: it is a user
    # action, and the change goes up on the next interval regardless.
    card.refresh()

    assert env.pushes == []


def test_refresh_without_settings_does_not_raise(env):
    card = _make_card(env)
    card.settings_obj = None
    env.clock.advance(_interval(env))
    env.pushes.clear()

    card.refresh()  # must not raise

    assert env.pushes == []


# --------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------


def test_database_failure_is_reported_not_raised(env, capsys):
    card = _make_card(env)
    env.db.raise_on_execute = True

    assert card.sync_leaderboard(force=True) is False
    assert "leaderboard" in capsys.readouterr().out.lower()


def test_database_failure_never_opens_a_dialog(env):
    card = _make_card(env)
    env.notifications.clear()
    env.db.raise_on_execute = True

    assert card.sync_leaderboard(force=True) is False

    # services.ui.notify() is a modal showInfo() in production. Reaching it
    # from here would block the review the player is in the middle of.
    assert env.notifications == []


def test_a_failed_highest_level_query_aborts_instead_of_publishing_a_zero(env):
    card = _make_card(env)
    env.pushes.clear()
    env.notifications.clear()
    # Only the highest-level query fails; every other query still succeeds, so
    # a payload could be built and sent.
    env.db.raise_on_highest_level_query = True

    assert card.sync_leaderboard(force=True) is False

    # highest_pokemon_level() would have swallowed this, popped a modal and
    # returned 0 — and find_trainer_rank turns a 0 into "Novice Trainer",
    # silently overwriting the player's real rank on the public leaderboard.
    assert env.pushes == []
    assert env.notifications == []


def test_missing_leaderboard_module_is_a_silent_no_op(env, monkeypatch):
    card = _make_card(env)
    # A None entry in sys.modules is the standard way to make an import raise
    # ImportError — deleting the key would just re-import the real module.
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.ankimon_leaderboard", None)
    env.pushes.clear()

    # The headless core / agent harness has no leaderboard module at all.
    assert card.sync_leaderboard(force=True) is False
    assert env.pushes == []


def test_leaderboard_module_failure_is_contained(env, monkeypatch):
    card = _make_card(env)

    def boom(_data):
        raise RuntimeError("leaderboard exploded")

    monkeypatch.setattr(
        sys.modules["Ankimon.pyobj.ankimon_leaderboard"],
        "sync_data_to_leaderboard",
        boom,
    )

    assert card.sync_leaderboard(force=True) is False


# --------------------------------------------------------------------------
# The gameplay hook: singletons.notify_stats_changed()
# --------------------------------------------------------------------------


class _RecordingCard:
    def __init__(self):
        self.syncs = 0
        self.raises = False

    def sync_leaderboard(self, force=False):
        self.syncs += 1
        if self.raises:
            raise RuntimeError("sync exploded")
        return True


@pytest.fixture
def singletons_env(monkeypatch):
    """Exec the real ``singletons.py`` against stubbed siblings (no Qt, no Anki).

    Mirrors the house pattern in ``test_reload_safe_singletons.py``: stub every
    module-level import, then load the real file so ``notify_stats_changed`` is
    the production function rather than a re-implementation.
    """
    trainer_card = _RecordingCard()
    core_objs = {name: MagicMock(name=name) for name in ("db", "logger", "settings", "translator", "tracker", "main_pokemon", "enemy_pokemon")}
    services = SimpleNamespace(
        trainer_card=trainer_card,
        ui=None,
        achievements=None,
        reviewer=None,
        **core_objs,
    )
    services.populate = lambda **kwargs: [setattr(services, k, v) for k, v in kwargs.items()]

    on_main_thread = {"value": True}
    refreshes = []

    class FakeLiveWindow:
        def objectName(self):
            return "items_web_window"

        def refresh_live_screen(self):
            refreshes.append(1)

    def is_alive(obj):
        if obj is None:
            return False
        try:
            obj.objectName()
            return True
        except (RuntimeError, AttributeError):
            return False

    monkeypatch.setitem(sys.modules, "aqt", _stub_module("aqt", mw=SimpleNamespace()))
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.utils",
        _stub_module(
            "Ankimon.utils",
            is_alive=is_alive,
            is_main_thread=lambda: on_main_thread["value"],
        ),
    )
    monkeypatch.setitem(
        sys.modules, "Ankimon.services", _stub_module("Ankimon.services", services=services)
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.resources",
        _stub_module("Ankimon.resources", addon_dir=Path("/tmp/addon")),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.core",
        _stub_module(
            "Ankimon.core",
            build_core=lambda: SimpleNamespace(
                logger=core_objs["logger"],
                ankimon_db=core_objs["db"],
                settings_obj=core_objs["settings"],
                translator=core_objs["translator"],
                main_pokemon=core_objs["main_pokemon"],
                mainpokemon_empty=False,
                enemy_pokemon=core_objs["enemy_pokemon"],
                trainer_card=trainer_card,
                ankimon_tracker_obj=core_objs["tracker"],
                achievements={"1": False},
            ),
            bind_runtime_globals=lambda: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.gui_presenter",
        _stub_module("Ankimon.gui_presenter", QtPresenter=type("QtPresenter", (), {})),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.ankimon_shop",
        _stub_module("Ankimon.pyobj.ankimon_shop", PokemonShopManager=type("PokemonShopManager", (), {"__init__": lambda self, *a, **k: None})),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.reviewer_obj",
        _stub_module("Ankimon.pyobj.reviewer_obj", Reviewer_Manager=type("Reviewer_Manager", (), {"__init__": lambda self, *a, **k: None})),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.singletons", _src / "Ankimon" / "singletons.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    return SimpleNamespace(
        module=module,
        trainer_card=trainer_card,
        services=services,
        on_main_thread=on_main_thread,
        refreshes=refreshes,
        live_window=FakeLiveWindow,
    )


def test_stat_change_syncs_the_leaderboard_with_no_window_open(singletons_env):
    singletons_env.module._items_web_window = None

    singletons_env.module.notify_stats_changed()

    # The leaderboard is not a screen: it must update whether or not the shell
    # happens to be open, so the push runs before the live-screen early-return.
    assert singletons_env.trainer_card.syncs == 1
    assert singletons_env.refreshes == []


def test_stat_change_syncs_the_leaderboard_and_the_live_screen(singletons_env):
    singletons_env.module._items_web_window = singletons_env.live_window()

    singletons_env.module.notify_stats_changed()

    assert singletons_env.trainer_card.syncs == 1
    assert singletons_env.refreshes == [1]


def test_background_threads_do_not_sync(singletons_env):
    singletons_env.on_main_thread["value"] = False

    singletons_env.module.notify_stats_changed()

    assert singletons_env.trainer_card.syncs == 0


def test_no_trainer_card_is_a_no_op(singletons_env):
    singletons_env.services.trainer_card = None
    singletons_env.module._items_web_window = None

    singletons_env.module.notify_stats_changed()  # must not raise


def test_a_failing_leaderboard_sync_does_not_block_the_live_refresh(singletons_env):
    singletons_env.trainer_card.raises = True
    singletons_env.module._items_web_window = singletons_env.live_window()

    singletons_env.module.notify_stats_changed()

    # A leaderboard error must not cost the player their live UI update.
    assert singletons_env.refreshes == [1]
