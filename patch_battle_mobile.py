import re

battle_path = 'src/Ankimon/battle_loop.py'
with open(battle_path, 'r') as f:
    battle_content = f.read()

old_battle = """        if cash_interval > 0 and total_reviews % cash_interval == 0:
            from datetime import date
            today_str = str(date.today())"""

new_battle = """        if cash_interval > 0 and total_reviews % cash_interval == 0:
            from .utils import get_current_anki_date_str
            today_str = get_current_anki_date_str()"""

battle_content = battle_content.replace(old_battle, new_battle)
with open(battle_path, 'w') as f:
    f.write(battle_content)

mobile_path = 'src/Ankimon/functions/mobile_sync.py'
with open(mobile_path, 'r') as f:
    mobile_content = f.read()

old_mobile1 = """    from datetime import date

    today_str = str(date.today())
    last_reward_date = ("""

new_mobile1 = """    from ..utils import get_current_anki_date_str

    today_str = get_current_anki_date_str()
    last_reward_date = ("""

old_mobile2 = """            # Enforce daily mobile cash cap
            from datetime import date

            today_str = str(date.today())"""

new_mobile2 = """            # Enforce daily mobile cash cap
            from ..utils import get_current_anki_date_str

            today_str = get_current_anki_date_str()"""

mobile_content = mobile_content.replace(old_mobile1, new_mobile1)
mobile_content = mobile_content.replace(old_mobile2, new_mobile2)

with open(mobile_path, 'w') as f:
    f.write(mobile_content)
