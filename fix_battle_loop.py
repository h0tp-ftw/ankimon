import re

with open("src/Ankimon/battle_loop.py", "r") as f:
    content = f.read()

# Remove 'import random' inside the block
content = re.sub(r'            import random\n', '', content)
# Remove 'from .services import services' inside the block
content = re.sub(r'                    from \.services import services\n', '', content)
# Remove 'from .utils import tooltip' inside the block
content = re.sub(r'                        from \.utils import tooltip\n', '', content)

with open("src/Ankimon/battle_loop.py", "w") as f:
    f.write(content)
