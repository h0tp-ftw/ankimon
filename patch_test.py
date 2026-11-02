import re

test_path = 'tests/test_mobile_sync.py'
with open(test_path, 'r') as f:
    content = f.read()

old_code = """    from datetime import date
    today_str = str(date.today())"""

new_code = """    from Ankimon.utils import get_current_anki_date_str
    today_str = get_current_anki_date_str()"""

content = content.replace(old_code, new_code)
with open(test_path, 'w') as f:
    f.write(content)
