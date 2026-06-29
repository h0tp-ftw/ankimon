"""
harness/server.py — a JSON-line REPL for driving a headless Ankimon session.

An agent (or a human) drives a live session by writing one JSON request per line
to stdin and reading one JSON response per line from stdout.

Protocol
--------
Request:   {"action": "<name>", ...kwargs}
Response:  {"ok": true,  "result": <return value of the action>}
           {"ok": false, "error": "<message>"}

Actions map 1:1 to Driver methods, e.g.:
    {"action": "answer", "ease": "good"}     # answer a card -> battle
    {"action": "catch"}                       # catch the fainted wild Pokemon
    {"action": "defeat"}                      # defeat it for XP
    {"action": "encounter"}                   # force a new wild encounter
    {"action": "get_state"}                   # snapshot of the world
    {"action": "drain_events"}                # events since last drain
    {"action": "set_setting", "key": "...", "value": ...}
    {"action": "set_move", "move": "Tackle"}
    {"action": "buy_item", "name": "potion"}
Plus control actions: {"action": "ping"} and {"action": "quit"}.

All Ankimon/engine stdout noise is routed to stderr so the JSON channel stays
clean. Run:
    python3 -m harness.server
    printf '{"action":"answer","ease":"good"}\\n{"action":"get_state"}\\n' | python3 harness/server.py
"""

import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main():
    # Keep the real stdout for JSON responses; send everything else to stderr so
    # the bundled engine's prints / config-save logs never corrupt the protocol.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    from harness.driver import Driver

    def respond(obj):
        real_stdout.write(json.dumps(obj) + "\n")
        real_stdout.flush()

    # Snappy default: a battle turn on every answer. The agent can change it
    # at runtime with set_setting.
    driver = Driver(settings_overrides={"battle.cards_per_round": 1})

    respond({"ok": True, "result": {
        "ready": True,
        "user_path": driver.env.user_path,
        "actions": [
            "answer", "catch", "defeat", "encounter", "set_setting", "set_move",
            "add_cash", "buy_item", "get_state", "drain_events", "ping", "quit",
        ],
        "state": driver.get_state(),
    }})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            respond({"ok": False, "error": f"bad json: {e}"})
            continue
        if not isinstance(req, dict):
            respond({"ok": False, "error": "request must be a JSON object"})
            continue

        action = req.pop("action", None)
        if action in ("quit", "exit"):
            respond({"ok": True, "result": "bye"})
            break
        if action == "ping":
            respond({"ok": True, "result": "pong"})
            continue
        if not action:
            respond({"ok": False, "error": "missing 'action'"})
            continue

        result = driver.act(action, **req)
        ok = not (isinstance(result, dict) and "error" in result)
        respond({"ok": ok, "result": result})


if __name__ == "__main__":
    main()
