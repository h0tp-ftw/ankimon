from harness.headless_env import start_session
env = start_session()

from Ankimon.poke_engine import constants
from Ankimon.poke_engine.objects import Pokemon
from Ankimon.functions.ankimon_hooks_to_poke_engine import _install_shell_bell

_install_shell_bell()
