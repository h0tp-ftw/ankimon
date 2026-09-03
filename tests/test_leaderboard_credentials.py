"""Security, migration, and request contracts for leaderboard credentials."""

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace


_SRC = Path(__file__).parent.parent / "src" / "Ankimon"
_SCHEMA_PATH = _SRC / "ankimon_items_web" / "settings_schema.py"
_LEADERBOARD_PATH = _SRC / "pyobj" / "ankimon_leaderboard.py"


def _load_settings_schema():
    spec = importlib.util.spec_from_file_location(
        "ankimon_settings_schema_credentials_test", _SCHEMA_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_leaderboard(monkeypatch, services):
    ankimon_pkg = types.ModuleType("Ankimon")
    ankimon_pkg.__path__ = [str(_SRC)]
    pyobj_pkg = types.ModuleType("Ankimon.pyobj")
    pyobj_pkg.__path__ = [str(_SRC / "pyobj")]
    services_mod = types.ModuleType("Ankimon.services")
    services_mod.services = services

    monkeypatch.setitem(sys.modules, "Ankimon", ankimon_pkg)
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj", pyobj_pkg)
    monkeypatch.setitem(sys.modules, "Ankimon.services", services_mod)

    spec = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.ankimon_leaderboard", _LEADERBOARD_PATH
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_secret_setting_serialization_never_exposes_stored_value():
    schema = _load_settings_schema()

    serialized = schema.serialize_secret_setting("actual-secret-api-key")

    assert "actual-secret-api-key" not in json.dumps(serialized)
    assert serialized == {
        "type": "password",
        "value": schema.SECRET_SETTING_PLACEHOLDER,
        "secret_configured": True,
        "secret_placeholder": schema.SECRET_SETTING_PLACEHOLDER,
    }


def test_empty_secret_serializes_as_unconfigured():
    schema = _load_settings_schema()

    serialized = schema.serialize_secret_setting("")

    assert serialized["value"] == ""
    assert serialized["secret_configured"] is False


def test_secret_placeholder_and_summary_redaction_contract():
    schema = _load_settings_schema()

    assert schema.is_unchanged_secret_placeholder(
        schema.SECRET_SETTING_PLACEHOLDER
    )
    assert not schema.is_unchanged_secret_placeholder("replacement-key")
    assert (
        schema.display_setting_value("leaderboard.api_key", "actual-secret-api-key")
        == schema.SECRET_SETTING_PLACEHOLDER
    )
    assert schema.display_setting_value("leaderboard.username", "Nuz") == "Nuz"


def test_migration_updates_live_settings_after_atomic_db_write(monkeypatch):
    class FakeSettings:
        def __init__(self):
            self.config = {
                "leaderboard.username": "",
                "leaderboard.api_key": "",
            }
            self.compute_calls = 0

        def compute_gui_config(self):
            self.compute_calls += 1

    class FakeDB:
        def migrate_user_data_to_config(self, key_map):
            assert key_map == {
                "username": "leaderboard.username",
                "api_key": "leaderboard.api_key",
            }
            return {
                "leaderboard.username": "legacy-user",
                "leaderboard.api_key": "legacy-secret",
            }

    settings = FakeSettings()
    module = _load_leaderboard(
        monkeypatch, SimpleNamespace(db=FakeDB(), settings=settings)
    )

    assert module.migrate_credentials_from_db() is True
    assert settings.config["leaderboard.username"] == "legacy-user"
    assert settings.config["leaderboard.api_key"] == "legacy-secret"
    assert settings.compute_calls == 1


def test_leaderboard_request_uses_settings_and_runs_off_caller_thread(monkeypatch):
    values = {
        "misc.leaderboard": True,
        "leaderboard.username": "Nuz",
        "leaderboard.api_key": "secret-key",
    }
    services = SimpleNamespace(
        db=object(),
        settings=SimpleNamespace(get=lambda key, default=None: values.get(key, default)),
    )
    module = _load_leaderboard(monkeypatch, services)

    calls = []

    def post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return SimpleNamespace(status_code=200)

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)

    module.sync_data_to_leaderboard({"caughtPokemon": 12})

    assert calls == [
        (
            module.ANKIMON_LEADERBOARD_API_URL,
            {
                "username": "Nuz",
                "api_key": "secret-key",
                "stats": {"caughtPokemon": 12},
            },
            10,
        )
    ]


def test_leaderboard_request_skips_when_credentials_are_missing(monkeypatch):
    values = {
        "misc.leaderboard": True,
        "leaderboard.username": "Nuz",
        "leaderboard.api_key": "",
    }
    services = SimpleNamespace(
        db=object(),
        settings=SimpleNamespace(get=lambda key, default=None: values.get(key, default)),
    )
    module = _load_leaderboard(monkeypatch, services)

    started = []
    monkeypatch.setattr(
        module.threading,
        "Thread",
        lambda **kwargs: started.append(kwargs),
    )

    module.sync_data_to_leaderboard({"caughtPokemon": 12})

    assert started == []


def _immediate_thread_module(module, monkeypatch):
    class ImmediateThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)


def test_repeated_pushes_log_only_on_a_change_of_outcome(monkeypatch, capsys):
    """The sync runs on the review path now, so it must not log a line a minute."""
    values = {
        "misc.leaderboard": True,
        "leaderboard.username": "Nuz",
        "leaderboard.api_key": "secret-key",
    }
    services = SimpleNamespace(
        db=object(),
        settings=SimpleNamespace(get=lambda key, default=None: values.get(key, default)),
    )
    module = _load_leaderboard(monkeypatch, services)
    _immediate_thread_module(module, monkeypatch)

    status = {"code": 200}
    monkeypatch.setattr(
        module.requests,
        "post",
        lambda url, *, json, timeout: SimpleNamespace(status_code=status["code"]),
    )

    for _ in range(5):
        module.sync_data_to_leaderboard({"caughtPokemon": 1})
    first = capsys.readouterr().out
    assert first.count("successfully") == 1, first

    # A change of outcome is still reported, once.
    status["code"] = 503
    for _ in range(3):
        module.sync_data_to_leaderboard({"caughtPokemon": 1})
    second = capsys.readouterr().out
    assert second.count("Status: 503") == 1, second

    # ...and so is the recovery.
    status["code"] = 200
    module.sync_data_to_leaderboard({"caughtPokemon": 1})
    assert capsys.readouterr().out.count("successfully") == 1


def test_credentials_are_never_printed(monkeypatch, capsys):
    values = {
        "misc.leaderboard": True,
        "leaderboard.username": "Nuz",
        "leaderboard.api_key": "super-secret-key",
    }
    services = SimpleNamespace(
        db=object(),
        settings=SimpleNamespace(get=lambda key, default=None: values.get(key, default)),
    )
    module = _load_leaderboard(monkeypatch, services)
    _immediate_thread_module(module, monkeypatch)

    def boom(url, *, json, timeout):
        raise module.requests.exceptions.RequestException(f"connection to {url} failed")

    monkeypatch.setattr(module.requests, "post", boom)
    module.sync_data_to_leaderboard({"caughtPokemon": 1})

    out = capsys.readouterr().out
    assert "super-secret-key" not in out
    assert "Nuz" not in out
