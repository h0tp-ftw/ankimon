"""
Regression test: ``gui.pop_up_dialog_message_on_item`` suppresses the item
pop-up WITHOUT dropping the item.

The trap this guards against: ``test_window.display_item()`` both rolls+grants
the reward (``random_item`` -> ``give_item``) *and* paints the QDialog. Gating
the call naively on the new setting would silently rob the user of the item
whenever they turned the pop-up off. ``battle_loop`` must fall through to
``random_item()`` instead, exactly as it already does for a dead window.

Isolation: same contract as ``test_headless_harness.py`` — the rest of the unit
suite stubs ``aqt``/``Ankimon.*`` in ``sys.modules`` at import time, so each
scenario runs in a CLEAN child interpreter and asserts on a JSON result.

Runs under pytest (CI) or as a plain script:  python3 tests/test_item_popup_toggle.py
"""

import json
import pathlib
import subprocess
import sys

_repo = pathlib.Path(__file__).resolve().parents[1]
_MARKER = "HARNESS_RESULT:"

# Force the Tier-1 contract in the child: NO Qt — see test_headless_harness.py.
_BLOCK_QT = (
    "import sys\n"
    "class _NoQt:\n"
    "    _b = ('aqt', 'PyQt6', 'PyQt5')\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in self._b:\n"
    "            raise ModuleNotFoundError(name + ' blocked: harness Tier-1 is Qt-free')\n"
    "        return None\n"
    "sys.meta_path.insert(0, _NoQt())\n"
)

# A stand-in for the seam window that is *alive* (objectName() works, so
# is_alive() returns True) and records whether display_item() was called.
# Deliberately does NOT grant an item, so the assertions can tell the popup
# path and the direct-grant path apart.
_SPY_WINDOW = (
    "class _SpyWindow:\n"
    "    def __init__(self):\n"
    "        self.display_item_calls = 0\n"
    "    def objectName(self):\n"
    "        return 'SpyWindow'\n"
    "    def display_item(self):\n"
    "        self.display_item_calls += 1\n"
    "    def __getattr__(self, name):\n"
    "        return lambda *a, **k: None\n"
)


def _subrun(body):
    """Run a scenario in a clean child interpreter, return its JSON result."""
    src = _BLOCK_QT + "import json\n" + body
    proc = subprocess.run(
        [sys.executable, "-c", src],
        cwd=str(_repo),
        capture_output=True,
        text=True,
        timeout=300,
    )
    for line in proc.stdout.splitlines():
        if line.startswith(_MARKER):
            return json.loads(line[len(_MARKER):])
    raise AssertionError(
        "child produced no result marker\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (proc.stdout, proc.stderr)
    )


def _scenario(popup_enabled):
    """Run the item-reward scenario with the popup enabled or disabled."""
    return _subrun(
        _SPY_WINDOW
        + "from harness.driver import Driver\n"
        + "d = Driver(settings_overrides={\n"
        + "    'battle.cards_per_round': 1,\n"
        + "    'gui.pop_up_dialog_message_on_item': %r,\n" % bool(popup_enabled)
        + "})\n"
        # Driver() runs harness bootstrap(), which is what makes the top-level
        # ``Ankimon`` package importable — so this import must come after it.
        + "from Ankimon import battle_loop\n"
        # random_item() picks the reward by listing the item sprite directory,
        # which the throwaway harness profile does not ship. Seed a few names so
        # the grant path can actually complete — otherwise random_item() raises
        # FileNotFoundError, battle_loop swallows it, and the test would pass
        # for the wrong reason (no item, but also no popup).
        + "import os\n"
        + "from Ankimon.utils import items_path\n"
        + "os.makedirs(items_path, exist_ok=True)\n"
        + "for _n in ('potion', 'ether', 'revive'):\n"
        + "    open(os.path.join(items_path, _n + '.png'), 'wb').close()\n"
        + "spy = _SpyWindow()\n"
        + "d.services.test_window = spy\n"
        + "before = len(d.services.db.get_all_items())\n"
        # Arm the trigger so the very next answered card fires the item branch.
        + "battle_loop._state.item_receive_value = 1\n"
        + "events = d.answer('good')\n"
        + "after = d.services.db.get_all_items()\n"
        + "errs = [e for e in events if e['type'] == 'error']\n"
        + "print(%r + json.dumps({\n" % _MARKER
        + "    'display_item_calls': spy.display_item_calls,\n"
        + "    'items_before': before,\n"
        + "    'items_after': len(after),\n"
        + "    'total_qty': sum(i.get('quantity', 0) for i in after),\n"
        + "    'errors': len(errs),\n"
        + "    'first_error': (errs[0].get('exception') if errs else None),\n"
        + "}))"
    )


def test_item_popup_disabled_still_grants_item():
    """
    Verify that disabling the item pop-up still awards the item without displaying the dialog.
    """
    r = _scenario(popup_enabled=False)
    assert r["errors"] == 0, "item branch raised: %s" % r["first_error"]
    assert r["display_item_calls"] == 0, (
        "display_item() was called with the pop-up setting disabled — "
        "the toggle does not actually suppress the dialog"
    )
    assert r["total_qty"] >= 1, (
        "no item was granted with the pop-up disabled — the reward was "
        "silently dropped along with the dialog (items_before=%s items_after=%s)"
        % (r["items_before"], r["items_after"])
    )


def test_item_popup_enabled_shows_dialog():
    """Setting ON (the default): behaviour is unchanged — the dialog is shown."""
    r = _scenario(popup_enabled=True)
    assert r["errors"] == 0, "item branch raised: %s" % r["first_error"]
    assert r["display_item_calls"] == 1, (
        "display_item() was not called with the pop-up setting enabled "
        "(calls=%s) — the default behaviour regressed" % r["display_item_calls"]
    )


def test_item_popup_setting_is_registered_everywhere():
    """The key must exist in the defaults AND be reachable from both settings
    surfaces. Both UIs resolve entries by *display name* via setting_name.json,
    so a missing name mapping ships the toggle invisible."""
    import importlib.util

    src = _repo / "src" / "Ankimon"
    key = "gui.pop_up_dialog_message_on_item"

    names = json.loads((src / "lang" / "setting_name.json").read_text(encoding="utf-8"))
    descs = json.loads(
        (src / "lang" / "setting_description.json").read_text(encoding="utf-8")
    )
    assert key in names, "%s missing from setting_name.json" % key
    assert key in descs, "%s missing from setting_description.json" % key
    label = names[key]

    config = json.loads((src / "config.json").read_text(encoding="utf-8"))
    assert config.get(key) is True, "%s should default to True in config.json" % key

    # settings_schema has no third-party imports, so it loads standalone.
    spec = importlib.util.spec_from_file_location(
        "_ankimon_item_popup_schema", src / "ankimon_items_web" / "settings_schema.py"
    )
    schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    web_labels = [
        s
        for g in schema.GROUPS
        for s in g.get("settings", [])
        if isinstance(s, str)
    ] + [
        s
        for g in schema.GROUPS
        for sub in g.get("subgroups", [])
        for s in sub.get("settings", [])
        if isinstance(s, str)
    ]
    assert label in web_labels, (
        "%r not listed in settings_schema.GROUPS — the toggle would be "
        "invisible in the web settings screen" % label
    )

    desktop = (src / "pyobj" / "settings_window.py").read_text(encoding="utf-8")
    assert '"%s"' % label in desktop, (
        "%r not listed in settings_window.py — the toggle would be invisible "
        "in the desktop settings window" % label
    )

    defaults = (src / "pyobj" / "settings.py").read_text(encoding="utf-8")
    assert '"%s": True' % key in defaults, (
        "%s missing from DEFAULT_CONFIG — existing users would not get the key" % key
    )


if __name__ == "__main__":
    test_item_popup_setting_is_registered_everywhere()
    test_item_popup_disabled_still_grants_item()
    test_item_popup_enabled_shows_dialog()
    print("item popup toggle tests: OK")
