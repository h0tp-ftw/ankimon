import re

with open("src/Ankimon/startup.py", "r") as f:
    content = f.read()

# Update _apply_first_enemy to expect 20 values
replace_pattern = r'(\s*nature,\n\s*\) = enemy_info)'
replacement = r'\n        nature,\n        is_trainer,\n        trainer_sprite,\n    ) = enemy_info'

content = re.sub(replace_pattern, replacement, content)

replace_update_stats = r'(\s*shiny=shiny,\n\s*nature=nature,\n\s*\))'
replacement_update_stats = r'\n        shiny=shiny,\n        nature=nature,\n        is_trainer=is_trainer,\n        trainer_sprite=trainer_sprite,\n    )'

content = re.sub(replace_update_stats, replacement_update_stats, content)

with open("src/Ankimon/startup.py", "w") as f:
    f.write(content)
