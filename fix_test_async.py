import re

with open("tests/test_async_startup_boot.py", "r") as f:
    content = f.read()

# Update test to return 20 values instead of 18
fix_pattern = r'("nature",)'
content = re.sub(fix_pattern, r'\1 False, None,', content)

with open("tests/test_async_startup_boot.py", "w") as f:
    f.write(content)
