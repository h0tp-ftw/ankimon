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
