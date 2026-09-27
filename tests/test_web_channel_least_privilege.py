"""Security boundary tests for the unified shell's QWebChannel objects."""

from pathlib import Path

import pytest

from Ankimon.ankimon_items_web.channel_policy import register_screen_bridges


ROOT = Path(__file__).resolve().parents[1]
SHOP_OBJ = ROOT / "src" / "Ankimon" / "ankimon_items_web" / "shop_obj.py"
HOST = ROOT / "src" / "Ankimon" / "webshell" / "host.py"

EXPECTED_BRIDGES = {
    "items": ("bridge", "nav"),
    "ankidex": ("nav",),
    "settings": ("settings", "nav"),
    "profile": ("trainer", "nav"),
    "team": ("team", "nav"),
    "mobile": ("mobile", "nav"),
    "history": ("mobile", "nav"),
}


class RecordingChannel:
    def __init__(self):
        self.registered = []

    def registerObject(self, name, obj):
        self.registered.append((name, obj))


@pytest.mark.parametrize("screen_id, expected_names", EXPECTED_BRIDGES.items())
def test_each_screen_receives_only_its_required_bridges(screen_id, expected_names):
    all_bridges = {
        name: object()
        for name in ("bridge", "nav", "settings", "trainer", "team", "mobile")
    }
    channel = RecordingChannel()

    registered = register_screen_bridges(channel, screen_id, all_bridges)

    assert registered == expected_names
    assert tuple(name for name, _ in channel.registered) == expected_names
    assert all(obj is all_bridges[name] for name, obj in channel.registered)
    assert set(dict(channel.registered)).isdisjoint(
        set(all_bridges) - set(expected_names)
    )


def test_unknown_screen_fails_closed():
    channel = RecordingChannel()

    with pytest.raises(ValueError, match="unknown web-shell screen"):
        register_screen_bridges(channel, "attacker-controlled", {"nav": object()})

    assert channel.registered == []


def test_missing_required_bridge_fails_before_registering_anything():
    channel = RecordingChannel()

    with pytest.raises(KeyError, match="missing bridge"):
        register_screen_bridges(channel, "profile", {"trainer": object()})

    assert channel.registered == []


def test_live_shell_uses_the_least_privilege_registration_policy():
    source = SHOP_OBJ.read_text(encoding="utf-8")

    assert "register_screen_bridges(channel, screen, bridges_by_name)" in source
    assert "channel.registerObject(" not in source


def test_generic_host_keeps_shared_live_bridge_separate_from_page_bridges():
    source = HOST.read_text(encoding="utf-8")

    assert 'channel.registerObject("live", self._live_bridge)' in source
    assert "for name, obj in (bridges or {}).items():" in source
    assert "channel.registerObject(name, obj)" in source
