import sys
import types

import pytest

from harness.scenarios import mega_fuzz
from harness.scenarios.mega_fuzz import _last_action


def test_last_action_ignores_trailing_error_and_rss_lines():
    lines = [
        "SEED 2 STEPS 40 WORLD seeded",
        "step 37: RIGHT-CLICK PokemonSlotButton 'pokemonSlot' in [Pokémon PC]",
        "    CONTEXT-ACTION 'Pick as main Pokémon'",
        "step 38: MENU 'Verify and Repair Database'",
        "  CAUGHT error event: generic diagnostic text",
        "RSS final: 350.0 MB (delta +20.0 over 40 steps)",
    ]

    assert _last_action(lines) == "step 38: MENU 'Verify and Repair Database'"


def test_last_action_can_report_a_context_menu_action():
    lines = [
        "SEED 7 STEPS 80 WORLD corrupt",
        "step 37: RIGHT-CLICK PokemonSlotButton 'pokemonSlot' in [Pokémon PC]",
        "    CONTEXT-ACTION 'Pick as main Pokémon'",
    ]

    assert _last_action(lines).strip() == "CONTEXT-ACTION 'Pick as main Pokémon'"


def test_last_action_handles_an_empty_journal():
    assert _last_action([]) == "(no journal written)"


@pytest.mark.parametrize("failure", [None, "teardown", "events"])
def test_child_exit_and_journal_reflect_teardown_result(monkeypatch, tmp_path, failure):
    calls = []

    def teardown(package):
        calls.append(package)
        if failure == "teardown":
            raise RuntimeError("failed to close windows")

    def process_events():
        calls.append("events")
        if failure == "events":
            raise RuntimeError("failed to flush events")

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.reloader",
        types.SimpleNamespace(teardown_ankimon=teardown),
    )
    monkeypatch.setattr(mega_fuzz.os, "_exit", exit_process)
    path = tmp_path / "journal.log"
    with path.open("w") as journal:
        with pytest.raises(SystemExit) as exc:
            mega_fuzz._finish_run(
                types.SimpleNamespace(processEvents=process_events),
                journal,
                lambda message: journal.write(message + "\n"),
                3,
            )
        assert journal.closed
    assert exc.value.code == (1 if failure else 0)
    assert calls == (["Ankimon"] if failure == "teardown" else ["Ankimon", "events"])
    output = path.read_text()
    assert ("SURVIVED" in output) is (failure is None)
    assert ("CAUGHT exception in teardown" in output) is (failure is not None)


@pytest.mark.parametrize("exit_code", [1, -11])
def test_parent_rejects_nonzero_exit_after_survived_marker(
    monkeypatch, capsys, exit_code
):
    monkeypatch.setattr(
        mega_fuzz,
        "_run_one",
        lambda *args: (
            0,
            exit_code,
            ["SEED 0 STEPS 3 WORLD seeded", "SURVIVED all 3 steps"],
        ),
    )
    failures = mega_fuzz.sweep(n_seeds=1, steps=3, world="seeded")
    assert len(failures) == 1
    assert failures[0][2] == exit_code
    assert (
        "process failed after completing all actions and teardown"
        in capsys.readouterr().out
    )
