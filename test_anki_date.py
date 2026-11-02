import sys
sys.path.insert(0, "src")
from datetime import datetime

def get_current_anki_date_str():
    try:
        from aqt import mw
        if mw and mw.col:
            # cutoff is tomorrow's rollover time in seconds since epoch
            cutoff = mw.col.sched.day_cutoff
            # the start of the current Anki day is cutoff - 86400
            start_of_day = cutoff - 86400
            # format the start of day as YYYY-MM-DD
            return datetime.fromtimestamp(start_of_day).strftime("%Y-%m-%d")
    except Exception:
        pass

    # Fallback to normal date if mw.col is not available
    return datetime.now().strftime("%Y-%m-%d")

print(get_current_anki_date_str())
