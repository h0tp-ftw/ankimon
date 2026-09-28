with open('tests/test_encounter_functions.py', 'r') as f:
    content = f.read()

import re

# Fix mock in test_handle_enemy_faint_auto_catch_regional_disabled
content = re.sub(
    r'        achievements = {}\n\n        # Execute\n        ef\.handle_enemy_faint\(',
    r'''        achievements = {}

        orig_load = getattr(ef, 'load_collected_pokemon_ids', None)
        ef.load_collected_pokemon_ids = mock.MagicMock(return_value={10091})
        import sys
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={10091})

        # Execute
        ef.handle_enemy_faint(''',
    content
)

content = re.sub(
    r'        assert mock_tracker\.faint_processed is True\n\n    finally:\n        # Restore original globals',
    r'''        assert mock_tracker.faint_processed is True

    finally:
        if orig_load is not None:
            ef.load_collected_pokemon_ids = orig_load
        # Restore original globals''',
    content
)


# Modify test_meets_prerequisites_fusion_and_normal
old_meets = '''    # 1. Test normal pokemon prerequisite (e.g. Mewtwo (150) needs Mew (151))
    assert ef._meets_prerequisites(150, {151}) is True
    assert ef._meets_prerequisites(150, set()) is False

    # 2. Test fusion forms (specific actual_id prerequisite, e.g. Necrozma Dusk Mane (10155) needs Necrozma (800) and Solgaleo (791))
    # It should not require Lunala (792) even though base Necrozma (800) requires Solgaleo and Lunala.
    assert ef._meets_prerequisites(10155, {800, 791}) is True
    assert ef._meets_prerequisites(10155, {800}) is False
    assert ef._meets_prerequisites(10155, {791}) is False

    # 3. Test fallback for forms not explicitly in PREREQUISITES (e.g. Aerodactyl Mega (10038) has base species Aerodactyl (142))
    # Aerodactyl has no prerequisites, so Aerodactyl Mega should meet prerequisites unconditionally.
    assert ef._meets_prerequisites(10038, set()) is True

    # 4. Test stat-redistribution forms requiring their base forms
    # Dialga Origin (10245) requires Dialga (483)
    assert ef._meets_prerequisites(10245, {483}) is True
    assert ef._meets_prerequisites(10245, set()) is False

    # Meloetta Pirouette (10018) requires Meloetta (648)
    assert ef._meets_prerequisites(10018, {648}) is True
    assert ef._meets_prerequisites(10018, set()) is False

    # 5. Test ("OR", {...}) prerequisites: any single member suffices
    # Terapagos (1024) requires Koraidon (1007) OR Miraidon (1008)
    assert ef._meets_prerequisites(1024, {1007}) is True
    assert ef._meets_prerequisites(1024, {1008}) is True
    assert ef._meets_prerequisites(1024, {1007, 1008}) is True
    assert ef._meets_prerequisites(1024, set()) is False'''

new_meets = '''    import sys
    try:
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={151})
        # 1. Test normal pokemon prerequisite (e.g. Mewtwo (150) needs Mew (151))
        assert ef._meets_prerequisites(150, {151}) is True

        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value=set())
        assert ef._meets_prerequisites(150, set()) is False

        # 2. Test fusion forms (specific actual_id prerequisite, e.g. Necrozma Dusk Mane (10155) needs Necrozma (800) and Solgaleo (791))
        # It should not require Lunala (792) even though base Necrozma (800) requires Solgaleo and Lunala.
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={800, 791})
        assert ef._meets_prerequisites(10155, {800, 791}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={800})
        assert ef._meets_prerequisites(10155, {800}) is False
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={791})
        assert ef._meets_prerequisites(10155, {791}) is False

        # 3. Test fallback for forms not explicitly in PREREQUISITES (e.g. Aerodactyl Mega (10038) has base species Aerodactyl (142))
        # Aerodactyl has no prerequisites, so Aerodactyl Mega should meet prerequisites unconditionally.
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value=set())
        assert ef._meets_prerequisites(10038, set()) is True

        # 4. Test stat-redistribution forms requiring their base forms
        # Dialga Origin (10245) requires Dialga (483)
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={483})
        assert ef._meets_prerequisites(10245, {483}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value=set())
        assert ef._meets_prerequisites(10245, set()) is False

        # Meloetta Pirouette (10018) requires Meloetta (648)
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={648})
        assert ef._meets_prerequisites(10018, {648}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value=set())
        assert ef._meets_prerequisites(10018, set()) is False

        # 5. Test ("OR", {...}) prerequisites: any single member suffices
        # Terapagos (1024) requires Koraidon (1007) OR Miraidon (1008)
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={1007})
        assert ef._meets_prerequisites(1024, {1007}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={1008})
        assert ef._meets_prerequisites(1024, {1008}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value={1007, 1008})
        assert ef._meets_prerequisites(1024, {1007, 1008}) is True
        sys.modules['Ankimon.utils'].load_collected_pokemon_ids = mock.MagicMock(return_value=set())
        assert ef._meets_prerequisites(1024, set()) is False
    finally:
        pass'''

content = content.replace(old_meets, new_meets)

with open('tests/test_encounter_functions.py', 'w') as f:
    f.write(content)
