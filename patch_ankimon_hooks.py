with open("src/Ankimon/functions/ankimon_hooks_to_poke_engine.py", "r") as f:
    content = f.read()

content = content.replace("""        if attacking_move_dict.get('ohko'):
            if attacker.level < defender.level:
                return [0]
            # OHKO bypasses normal damage formulas, but keeps its original accuracy (e.g. 30%)
            # unless affected by No Guard or Lock-On.
            return [defender.hp]""", """        ohko_val = attacking_move_dict.get('ohko')
        if ohko_val is not None and ohko_val is not False:
            if attacker.level < defender.level:
                return [0]
            # OHKO bypasses normal damage formulas, but keeps its original accuracy (e.g. 30%)
            # unless affected by No Guard or Lock-On.
            return [defender.hp]""")
content = content.replace("    orig_calc = find_state_instructions._calculate_damage", """    if hasattr(find_state_instructions, 'calculate_damage'):
        orig_calc = find_state_instructions.calculate_damage
    else:
        orig_calc = getattr(find_state_instructions, '_calculate_damage', None)""")
content = content.replace("    find_state_instructions._calculate_damage = new_calc\n    damage_calculator._calculate_damage = new_calc", """    if hasattr(find_state_instructions, 'calculate_damage'):
        find_state_instructions.calculate_damage = new_calc
    if hasattr(find_state_instructions, '_calculate_damage'):
        find_state_instructions._calculate_damage = new_calc
    if hasattr(damage_calculator, 'calculate_damage'):
        damage_calculator.calculate_damage = new_calc
    if hasattr(damage_calculator, '_calculate_damage'):
        damage_calculator._calculate_damage = new_calc""")
content = content.replace("        return orig_calc(attacker, defender, move, conditions, calc_type)", """        if orig_calc is not None:
            return orig_calc(attacker, defender, move, conditions, calc_type)
        return [0]""")

with open("src/Ankimon/functions/ankimon_hooks_to_poke_engine.py", "w") as f:
    f.write(content)
