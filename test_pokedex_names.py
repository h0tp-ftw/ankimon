import sys
import os

# Ensure the root src directory is in the python path
sys.path.insert(0, os.path.abspath('src'))

# Mocking all dependencies of pokedex_functions so we don't trigger Qt
from unittest.mock import MagicMock
sys.modules['aqt'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()

# Mocking the internal Ankimon modules that are imported relatively
class MockPackage(MagicMock):
    __path__ = []

sys.modules['Ankimon'] = MockPackage()
sys.modules['Ankimon.resources'] = MagicMock()
sys.modules['Ankimon.pyobj'] = MockPackage()
sys.modules['Ankimon.pyobj.error_handler'] = MagicMock()

# Need to prevent Ankimon/__init__.py from executing when importing functions
# We'll just read the function code and exec it
with open('src/Ankimon/functions/pokedex_functions.py', 'r') as f:
    code = f.read()

# Execute the code in a dummy namespace
namespace = {}
# Create dummy dependencies needed by the module
namespace['showWarning'] = MagicMock()
namespace['mw'] = MagicMock()
namespace['show_warning_with_traceback'] = MagicMock()

# Instead of execing the whole module (which has relative imports), let's just
# extract and exec the special_pokemon_names_for_min_level function
func_code = ""
in_func = False
for line in code.split('\n'):
    if line.startswith('def special_pokemon_names_for_min_level(name):'):
        in_func = True
        func_code += line + '\n'
    elif in_func and line.startswith('def '):
        break
    elif in_func:
        func_code += line + '\n'

exec(func_code, namespace)
func = namespace['special_pokemon_names_for_min_level']

# Test cases
tests = {
    'ho-oh': 'hooh',
    'tapu-koko': 'tapukoko',
    'tapu-lele': 'tapulele',
    'tapu-bulu': 'tapubulu',
    'tapu-fini': 'tapufini',
    'ting-lu': 'tinglu',
    'chien-pao': 'chienpao',
    'wo-chien': 'wochien',
    'chi-yu': 'chiyu',
    'type-null': 'typenull',
    'type: null': 'typenull',
    'flabébé': 'flabebe',
    'porygon-z': 'porygonz'
}

all_passed = True
for name, expected in tests.items():
    result = func(name)
    if result != expected:
        print(f'FAIL: {name} -> expected {expected}, got {result}')
        all_passed = False

if all_passed:
    print('All manually tested special names passed!')
