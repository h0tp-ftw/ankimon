import subprocess
import os

def run_cmd(cmd):
    print("Running:", cmd)
    subprocess.run(cmd, shell=True, check=True)

# Formatters
run_cmd("autoflake --in-place --remove-all-unused-imports --remove-unused-variables -r src/Ankimon/")
run_cmd("isort src/Ankimon/")

with open('src/Ankimon/poke_engine/battle.py', 'r') as f:
    lines = f.readlines()

lines[323] = '    """Docstring."""\n'

with open('src/Ankimon/poke_engine/battle.py', 'w') as f:
    f.writelines(lines)

run_cmd("black src/Ankimon/")
