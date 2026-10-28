import re

utils_path = 'src/Ankimon/utils.py'
with open(utils_path, 'r') as f:
    content = f.read()

# Replace the fallback format in get_current_anki_date_str to force proper format if str(date) isn't behaving across environments.
old_code = """def get_current_anki_date_str():
    \"\"\"
    Returns the current Anki date as a YYYY-MM-DD string.
    \"\"\"
    return str(get_current_anki_date())"""

new_code = """def get_current_anki_date_str():
    \"\"\"
    Returns the current Anki date as a YYYY-MM-DD string.
    \"\"\"
    return get_current_anki_date().strftime("%Y-%m-%d")"""

content = content.replace(old_code, new_code)
with open(utils_path, 'w') as f:
    f.write(content)
