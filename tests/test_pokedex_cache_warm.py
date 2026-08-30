"""``pokedex.json`` must be parsed off the GUI thread, and never memoized empty.

``_load_pokedex_cache`` is lazy, so its FIRST caller pays the ~800 KB read and
parse. One of those callers is the Ankidex: ``build_encounterable_ids`` walks the
roll's tier lists through ``search_pokedex_by_id`` / ``check_min_generate_level``,
and it runs inside ``Ankidex.showEvent`` — on the GUI thread. The boot's
first-enemy roll usually warms the cache first, but not when the sprite folders
are incomplete (that step is gated on ``database_complete``) and not after a
profile switch, which clears the caches without re-running the once-per-process
boot. ``warm_pokedex_caches`` closes both gaps, on the same background thread
that already warms ``pokemon_evolution.csv``.

Moving the first read earlier is only safe with the guard tested below: both
loaders memoize an empty dict when the read fails, which would answer "no such
Pokemon" to every lookup for the rest of the session.

Loading strategy mirrors ``tests/test_evolution_gender_gate.py``: stub Anki/aqt,
load ``resources`` + ``pokedex_functions`` FOR REAL so the bundled data drives
every lookup.
"""

import builtins
import importlib.util
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"


class _FakeSettings:
    def __init__(self):
        self.values = {"misc.active_region": None}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _load_pf():
    sys.modules["aqt"] = mock.MagicMock()
    sys.modules["aqt.qt"] = mock.MagicMock()
    sys.modules["aqt.utils"] = mock.MagicMock()
    sys.modules["Ankimon.pyobj.error_handler"] = mock.MagicMock()

    singletons_stub = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("Ankimon.singletons", loader=None)
    )
    singletons_stub.settings_obj = _FakeSettings()
    sys.modules["Ankimon.singletons"] = singletons_stub

    res_spec = importlib.util.spec_from_file_location(
        "Ankimon.resources", _SRC / "Ankimon" / "resources.py"
    )
    resources = importlib.util.module_from_spec(res_spec)
    sys.modules["Ankimon.resources"] = resources
    res_spec.loader.exec_module(resources)

    pf_spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.pokedex_functions",
        _SRC / "Ankimon" / "functions" / "pokedex_functions.py",
    )
    pokedex_functions = importlib.util.module_from_spec(pf_spec)
    sys.modules["Ankimon.functions.pokedex_functions"] = pokedex_functions
    pf_spec.loader.exec_module(pokedex_functions)
    return pokedex_functions


pf = _load_pf()

_SINGLETONS_STUB = sys.modules["Ankimon.singletons"]
_POKEDEX_FUNCTIONS_STUB = sys.modules["Ankimon.functions.pokedex_functions"]


@pytest.fixture(autouse=True)
def _reset_env():
    sys.modules["Ankimon.singletons"] = _SINGLETONS_STUB
    sys.modules["Ankimon.functions.pokedex_functions"] = _POKEDEX_FUNCTIONS_STUB
    pf.clear_pokedex_caches()
    yield
    pf.clear_pokedex_caches()


def _count_pokedex_opens(fn):
    """Run ``fn``, returning its result and every pokedex.json path it opened."""
    original = builtins.open
    opens = []

    def counting_open(file, *args, **kwargs):
        if str(file).endswith("pokedex.json"):
            opens.append(str(file))
        return original(file, *args, **kwargs)

    builtins.open = counting_open
    try:
        return fn(), opens
    finally:
        builtins.open = original


def _fail_to_open_the_pokedex(fn):
    """Run ``fn`` with every open of pokedex.json raising."""
    original = builtins.open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith("pokedex.json"):
            raise OSError("simulated read failure")
        return original(file, *args, **kwargs)

    builtins.open = failing_open
    try:
        return fn()
    finally:
        builtins.open = original


def test_warming_takes_the_pokedex_parse_off_the_window_open():
    assert pf._pokedex_cache is None
    assert pf._pokedex_id_index is None

    entries = pf.warm_pokedex_caches()

    assert entries > 1000, "the bundled pokedex.json carries ~1384 entries"
    assert pf._pokedex_cache is not None
    # The index too: search_pokedex_by_id goes through it, and building it
    # lazily on the GUI thread is the same stall in a smaller coat.
    assert pf._pokedex_id_index is not None

    # With the warm done, the Ankidex's own lookups open nothing at all.
    _, opens = _count_pokedex_opens(
        lambda: [pf.search_pokedex_by_id(i) for i in (1, 150, 10043, 10186)]
    )
    assert opens == [], f"a warmed lookup path still parsed pokedex.json {opens}"


def test_a_failed_warm_leaves_the_lazy_path_to_retry_instead_of_memoizing_empty():
    """The warm is an optimization; it must not add a failure mode.

    ``_load_pokedex_cache`` swallows a read error and memoizes ``{}`` for the
    session, and ``_load_pokedex_id_index`` memoizes a second ``{}`` built on top
    of it. Lazily that is survivable — the first read happens whenever the game
    needs the data. Warming at boot means a file that is momentarily unavailable
    *then* (an add-on update mid-write, a cold network drive) would freeze
    "Pokémon not found" in for every lookup for the rest of the session.
    """
    assert _fail_to_open_the_pokedex(pf.warm_pokedex_caches) == 0

    assert pf._pokedex_cache is None
    # Not `{}`: an index memoized from the failed load would keep answering
    # "Pokémon not found" even after the cache itself was released.
    assert pf._pokedex_id_index is None

    # Nothing was memoized: the ordinary lazy path still reads real data.
    assert pf.search_pokedex_by_id(150) == "mewtwo"


def test_warming_twice_is_a_no_op_that_keeps_the_same_cache():
    """Boot warms once per process and the profile-open hook warms again after a
    profile switch cleared the caches, so back-to-back warms are normal. The
    second must not re-parse — callers hold references INTO the cached dicts."""
    first_count = pf.warm_pokedex_caches()
    first_cache = pf._pokedex_cache

    second_count, opens = _count_pokedex_opens(pf.warm_pokedex_caches)

    assert second_count == first_count
    assert opens == []
    assert pf._pokedex_cache is first_cache


def test_the_warm_is_undone_by_a_profile_close():
    """``_on_profile_close`` -> ``clear_pokedex_caches`` is why the profile-open
    hook has to warm again; pin that the clear really does drop both globals."""
    pf.warm_pokedex_caches()

    pf.clear_pokedex_caches()

    assert pf._pokedex_cache is None
    assert pf._pokedex_id_index is None
