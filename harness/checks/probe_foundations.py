"""Import-safety probe for the foundation modules (Task 1).

Proves that events / ui_port / services import and behave correctly under plain
python3 with no aqt/PyQt6. Run:  python3 harness/checks/probe_foundations.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.bootstrap import bootstrap

bootstrap(user_path="/tmp/ankimon_harness_probe")

from Ankimon.events import events
from Ankimon import ui_port
from Ankimon.services import services


def main() -> int:
    # events: silent until enabled
    events.disable()
    events.reset()
    events.emit("ignored", x=1)
    assert events.peek() == [], "emit must be a no-op while disabled"

    events.enable()
    events.emit("catch", pokemon="Rattata", id=19)
    drained = events.drain()
    assert len(drained) == 1 and drained[0]["type"] == "catch", drained
    assert drained[0]["pokemon"] == "Rattata" and "seq" in drained[0] and "ts" in drained[0]
    assert events.drain() == [], "drain must clear the buffer"

    # ui_port headless presenter
    p = ui_port.HeadlessPresenter()
    assert p.choose_move(["a", "b"]) is None
    p.next_move = "b"
    assert p.choose_move(["a", "b"]) == "b"
    assert p.choose_attack_to_replace(["x", "y"], "z") is None  # default reject
    p.replace_policy = "first"
    assert p.choose_attack_to_replace(["x", "y"], "z") == "x"
    p.replace_policy = "y"
    assert p.choose_attack_to_replace(["x", "y"], "z") == "y"

    # services: default ui present, populate/reset
    assert isinstance(services.ui, ui_port.HeadlessPresenter)
    services.reset()
    assert services.db is None and isinstance(services.ui, ui_port.HeadlessPresenter)

    services.populate(settings={"flag": True}, db="FAKE_DB")
    assert services.settings == {"flag": True}
    assert services.db == "FAKE_DB"

    events.disable()
    services.reset()
    print("probe_foundations: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
