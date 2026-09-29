#!/usr/bin/env python3
"""Proof for PR 883: macOS Discord IPC parent lookup and closed-pipe recovery.

The PR's unit tests mock ``test_ipc_path`` so a regular file counts as a
socket. This proof binds a real Unix socket in the parent of the temp
directory, then drives ``DiscordPresence`` through a closed pipe.
"""

import os
import socket
import sys
import tempfile
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

for candidate in (*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents):
    if (candidate / "harness" / "driver.py").is_file() and (
        candidate / "src" / "Ankimon"
    ).is_dir():
        REPO_ROOT = candidate
        break
else:
    raise RuntimeError(
        "Save this proof inside the Ankimon checkout or run it from the checkout root."
    )
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.bootstrap import bootstrap  # noqa: E402

bootstrap()

from Ankimon.addon_files.lib.pypresence.exceptions import PipeClosed  # noqa: E402
from Ankimon.addon_files.lib.pypresence.utils import get_ipc_path  # noqa: E402
from Ankimon.events import events  # noqa: E402
from Ankimon.functions import discord_function as discord  # noqa: E402
from Ankimon.functions.encounter_functions import (  # noqa: E402
    USE_OVERHAUL_ENCOUNTER_SYSTEM,
    _modify_percentages_legacy,
    get_all_pokemon_in_tier,
)
import Ankimon.addon_files.lib.pypresence.utils as ipc_utils  # noqa: E402


class _ListLogger:
    def __init__(self):
        self.records = []

    def log(self, level, message):
        self.records.append((level, message))


class _ListeningSocket:
    def __init__(self, path: Path):
        if path.exists():
            path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        self.path = path
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            conn.close()

    def close(self):
        self._sock.close()
        try:
            self.path.unlink()
        except OSError:
            pass


def _force_temp_fallback(runtime_dir: Path):
    """Make get_ipc_path use ``runtime_dir`` the way macOS does without XDG."""
    previous = {
        "platform": ipc_utils.sys.platform,
        "gettempdir": ipc_utils.tempfile.gettempdir,
        "exists": ipc_utils.os.path.exists,
        "xdg": os.environ.get("XDG_RUNTIME_DIR"),
    }
    os.environ.pop("XDG_RUNTIME_DIR", None)
    ipc_utils.sys.platform = "darwin"
    ipc_utils.tempfile.gettempdir = lambda: str(runtime_dir)
    real_exists = previous["exists"]
    ipc_utils.os.path.exists = (
        lambda path: False
        if str(path).startswith("/run/user/")
        else real_exists(path)
    )
    return previous


def _restore_temp_fallback(previous):
    ipc_utils.sys.platform = previous["platform"]
    ipc_utils.tempfile.gettempdir = previous["gettempdir"]
    ipc_utils.os.path.exists = previous["exists"]
    if previous["xdg"] is None:
        os.environ.pop("XDG_RUNTIME_DIR", None)
    else:
        os.environ["XDG_RUNTIME_DIR"] = previous["xdg"]


def _prove_parent_socket_is_found():
    with tempfile.TemporaryDirectory(prefix="ankimon-ipc-") as tmp:
        base = Path(tmp)
        runtime = base / "T"
        runtime.mkdir()
        parent_socket = _ListeningSocket(base / "discord-ipc-0")
        previous = _force_temp_fallback(runtime)
        try:
            found = get_ipc_path()
        finally:
            _restore_temp_fallback(previous)
            parent_socket.close()
        assert found == str(parent_socket.path), found
    print(">>> Parent-of-tempdir Unix socket was discovered with a real connect.")


def _prove_native_socket_wins():
    with tempfile.TemporaryDirectory(prefix="ankimon-ipc-") as tmp:
        base = Path(tmp)
        runtime = base / "runtime"
        runtime.mkdir()
        native = _ListeningSocket(runtime / "discord-ipc-0")
        parent = _ListeningSocket(base / "discord-ipc-0")
        previous_platform = ipc_utils.sys.platform
        previous_xdg = os.environ.get("XDG_RUNTIME_DIR")
        ipc_utils.sys.platform = "darwin"
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        try:
            found = get_ipc_path()
        finally:
            ipc_utils.sys.platform = previous_platform
            if previous_xdg is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = previous_xdg
            native.close()
            parent.close()
        assert found == str(native.path), found
    print(">>> Native socket was preferred over the parent-directory socket.")


def _presence(logger):
    presence = discord.DiscordPresence(
        "client-id",
        "image",
        object(),
        logger,
        types.SimpleNamespace(get=lambda _key: 1),
    )
    presence._checked_conflicts = True
    presence._first_attempt = False
    presence._last_connect_attempt = float("-inf")
    presence.start_time = 12345.0
    return presence


def _prove_closed_pipe_reconnects_without_tooltip():
    events.enable()
    events.reset()
    logger = _ListLogger()
    presence = _presence(logger)
    closed = types.SimpleNamespace(
        update=MagicMock(side_effect=PipeClosed()),
        close=MagicMock(),
    )
    presence.connected = True
    presence.loop = True
    presence.RPC = closed

    presence.update_presence()

    assert presence.loop is False
    assert presence.connected is False
    assert presence.RPC is None
    assert presence.start_time == 12345.0
    closed.close.assert_called_once_with()
    assert logger.records
    assert all(level == "warning" for level, _message in logger.records)
    assert events.drain() == []

    clients = []

    class _RPC:
        def __init__(self, client_id):
            self.client_id = client_id
            self.updates = []
            clients.append(self)

        def connect(self):
            return None

        def update(self, **kwargs):
            self.updates.append(kwargs)

        def close(self):
            return None

    discord.Presence = _RPC
    original_sleep = discord.time.sleep
    discord.time.sleep = lambda _seconds: setattr(presence, "loop", False)
    try:
        presence.start()
        presence.thread.join(timeout=2)
    finally:
        discord.time.sleep = original_sleep

    assert presence.thread is not None and not presence.thread.is_alive()
    assert presence.connected is True
    assert presence.RPC is clients[0]
    assert len(clients[0].updates) == 1
    assert presence.start_time == 12345.0
    assert events.drain() == []
    print(">>> Closed pipe logged a warning, then the next start() reconnected.")


def _prove_sleeping_worker_reconnects_after_clear():
    events.enable()
    events.reset()
    logger = _ListLogger()
    presence = _presence(logger)
    parked = threading.Event()
    release = threading.Event()
    updates = []
    sleeps = {"count": 0}

    class _OldRPC:
        def update(self, **_kwargs):
            updates.append("old")
            parked.set()

        def clear(self):
            raise PipeClosed()

        def close(self):
            raise AssertionError("main-thread clear must not close the client")

    class _NewRPC:
        def __init__(self, client_id):
            self.client_id = client_id

        def connect(self):
            return None

        def update(self, **_kwargs):
            updates.append("new")

        def close(self):
            return None

    def sleep(_seconds):
        sleeps["count"] += 1
        if sleeps["count"] == 1:
            assert release.wait(2)
        else:
            presence.loop = False

    presence.connected = True
    presence.RPC = _OldRPC()
    discord.Presence = _NewRPC
    original_sleep = discord.time.sleep
    discord.time.sleep = sleep
    try:
        presence.start()
        assert parked.wait(2)
        worker = presence.thread
        presence.stop()
        assert presence.connected is False
        assert presence.RPC is None
        presence.start()
        assert presence.thread is worker
        release.set()
        worker.join(timeout=2)
    finally:
        discord.time.sleep = original_sleep

    assert not worker.is_alive()
    assert updates == ["old", "new"]
    assert isinstance(presence.RPC, _NewRPC)
    assert presence.connected is True
    assert presence.start_time == 12345.0
    assert any(level == "warning" for level, _message in logger.records)
    assert events.drain() == []
    print(">>> A pipe closed during clear reconnected the same sleeping worker.")


def _prove_wild_starters_stay_disabled():
    assert USE_OVERHAUL_ENCOUNTER_SYSTEM is False
    assert get_all_pokemon_in_tier("Starter") == []
    # Level 100, a long review streak, and a high trainer level are the
    # conditions #841 used to turn Starter chance back on.
    percentages = _modify_percentages_legacy(1000, 10, 50, main_level=100)
    assert percentages["Starter"] == 0, percentages
    print(">>> Wild Starter pool and legacy chance are still disabled.")


def run_proof():
    _prove_parent_socket_is_found()
    _prove_native_socket_wins()
    _prove_closed_pipe_reconnects_without_tooltip()
    _prove_sleeping_worker_reconnects_after_clear()
    _prove_wild_starters_stay_disabled()
    print("✅ PR 883 proof PASSED")
    return True


if __name__ == "__main__":
    success = run_proof()
    sys.exit(0 if success else 1)
