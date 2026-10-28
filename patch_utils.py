import re

utils_path = 'src/Ankimon/utils.py'
with open(utils_path, 'r') as f:
    content = f.read()

# Add the import and function at the bottom
new_code = """

def get_current_anki_date():
    \"\"\"
    Returns the current Anki date based on the user's rollover time.
    If Anki is unavailable, falls back to the system's current date.
    Returns a datetime.date object.
    \"\"\"
    from datetime import datetime
    try:
        from aqt import mw
        if mw and mw.col:
            # cutoff is tomorrow's rollover time in seconds since epoch
            cutoff = mw.col.sched.day_cutoff
            # the start of the current Anki day is cutoff - 86400
            start_of_day = cutoff - 86400
            return datetime.fromtimestamp(start_of_day).date()
    except Exception:
        pass
    return datetime.today().date()

def get_current_anki_date_str():
    \"\"\"
    Returns the current Anki date as a YYYY-MM-DD string.
    \"\"\"
    return str(get_current_anki_date())
"""

if "def get_current_anki_date" not in content:
    with open(utils_path, 'a') as f:
        f.write(new_code)
