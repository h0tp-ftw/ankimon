import re

with open("tests/test_async_startup_boot.py", "r") as f:
    content = f.read()

# I accidentally changed the mock list of attacks
replace_pattern = r'\["thunder-shock", "growl", "tail-whip"\]'
replacement = r'["thunder-shock"]'

content = re.sub(replace_pattern, replacement, content)

with open("tests/test_async_startup_boot.py", "w") as f:
    f.write(content)
