"""Shared test configuration.

Stub the Ankimon package in sys.modules so that individual submodules can be
imported without triggering Ankimon/__init__.py, which depends on Anki internals.
"""

import contextlib
import sys
import types
from pathlib import Path

_src = Path(__file__).parent.parent / "src"

# Stub parent packages so relative imports resolve without loading __init__.py
for _pkg in (
    "Ankimon",
    "Ankimon.functions",
    "Ankimon.pyobj",
    "Ankimon.ankimon_items_web",
):
    if _pkg not in sys.modules:
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

import pytest


@pytest.fixture(autouse=True)
def restore_package_stubs():
    from unittest.mock import MagicMock

    def do_restore():
        # After each test, restore the stubs if they were replaced by MagicMock/None/etc.
        for _pkg in (
            "Ankimon",
            "Ankimon.functions",
            "Ankimon.pyobj",
            "Ankimon.ankimon_items_web",
        ):
            current = sys.modules.get(_pkg)
            if (
                current is None
                or not hasattr(current, "__path__")
                or not isinstance(current, types.ModuleType)
                or isinstance(current, MagicMock)
            ):
                _mod = types.ModuleType(_pkg)
                _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
                _mod.__package__ = _pkg
                sys.modules[_pkg] = _mod

        # Link sub-packages to parent packages so attribute access works
        if "Ankimon" in sys.modules:
            for attr in ("functions", "pyobj", "ankimon_items_web"):
                subpkg = f"Ankimon.{attr}"
                if subpkg in sys.modules:
                    setattr(sys.modules["Ankimon"], attr, sys.modules[subpkg])

        # Also restore Ankimon.resources to the real resources module if it was mocked with tmp paths
        current_res = sys.modules.get("Ankimon.resources")
        if (
            current_res is None
            or not isinstance(current_res, types.ModuleType)
            or isinstance(current_res, MagicMock)
            or not hasattr(current_res, "pokedex_path")
            or "tmp" in str(getattr(current_res, "pokedex_path", ""))
            or getattr(current_res, "pokedex_path", "") == "dummy"
        ):
            import importlib.util

            res_spec = importlib.util.spec_from_file_location(
                "Ankimon.resources", _src / "Ankimon" / "resources.py"
            )
            resources = importlib.util.module_from_spec(res_spec)
            sys.modules["Ankimon.resources"] = resources
            try:
                res_spec.loader.exec_module(resources)
            except Exception:
                # Never leave a partially-initialized module in sys.modules —
                # it would poison every subsequent import.  Put back whatever
                # was there before (or drop the entry) and surface the error.
                if current_res is not None:
                    sys.modules["Ankimon.resources"] = current_res
                else:
                    sys.modules.pop("Ankimon.resources", None)
                raise

        # Also restore Ankimon.utils if it is a MagicMock or a partial stub.
        # The sentinel-attribute check mirrors the pokedex_path check above:
        # some tests install a plain types.ModuleType stub carrying only the
        # one function they fake (e.g. give_item), which would otherwise
        # leak and break any later test that lazily imports real helpers.
        current_utils = sys.modules.get("Ankimon.utils")
        if (
            current_utils is None
            or isinstance(current_utils, MagicMock)
            or not hasattr(current_utils, "get_ev_spread")
        ):
            import importlib.util

            utils_spec = importlib.util.spec_from_file_location(
                "Ankimon.utils", _src / "Ankimon" / "utils.py"
            )
            utils_mod = importlib.util.module_from_spec(utils_spec)
            sys.modules["Ankimon.utils"] = utils_mod
            try:
                utils_spec.loader.exec_module(utils_mod)
            except Exception:
                # Never leave a partially-initialized module in sys.modules —
                # it would poison every subsequent import.  Put back whatever
                # was there before (or drop the entry) and surface the error.
                if current_utils is not None:
                    sys.modules["Ankimon.utils"] = current_utils
                else:
                    sys.modules.pop("Ankimon.utils", None)
                raise

        # NOTE (main re-fit): exp additionally re-executed the real
        # ``Ankimon.singletons`` module here.  On main, singletons.py is the
        # production Qt/DB composition root: at module level it runs
        # ``build_core()`` and constructs SettingsWindow / TestWindow /
        # Reviewer_Manager / EvoWindow / PokemonPC / ... .  Re-exec'ing it in a
        # headless test session hangs (it blocks constructing Qt windows), which
        # is exactly why tests/test_addon_integrity.py keeps it pre-stubbed.
        # Restoring it to the *real* module would require reload-safe singletons
        # (Stage-B F31/F32), which is not on this branch yet, so we intentionally
        # do NOT re-exec it here.  A test that mocks singletons simply keeps its
        # mock; nothing headless imports the real composition root.

    do_restore()
    yield
    do_restore()


# Sentinel for "this parent package had no such attribute before the block".
_MISSING = object()


@contextlib.contextmanager
def isolated_modules(*prefixes, extra=()):
    """Clear the named module namespaces, then restore ``sys.modules`` EXACTLY.

    Several tests have to import a module against the *genuine* PyQt6 (and a
    synthetic ``aqt``) even though earlier suite modules have replaced those
    entries with ``MagicMock``s. Dropping the mocks is the easy half; putting
    things back is the half that is easy to get wrong.

    Restoring only the saved keys is not enough. A block that drops ``PyQt6.*``
    so the real package can load leaves the freshly imported submodules behind,
    so a later test that installs a mocked ``PyQt6`` parent ends up with that
    mock and genuine children such as ``PyQt6.QtWebChannel`` hanging off it —
    order-dependent, and dependent on which test ran first. Likewise a fixture
    that installs synthetic ``aqt``/``aqt.qt``/``aqt.utils`` modules must take
    them away again.

    So: on entry every module whose top-level name is in ``prefixes`` (plus any
    exact name in ``extra``) is removed; on exit everything added under those
    same names is removed and the original entries are put back, along with the
    parent-package ATTRIBUTE for any tracked module whose parent outlives the
    block (see the comment on ``saved_attrs`` — ``sys.modules`` alone leaves
    ``Ankimon.pyobj.InfoLogger`` and ``sys.modules[...]`` disagreeing).
    Untracked namespaces are left alone. Exceptions still restore, so an import
    failure inside the block cannot leak state either.

    Args:
        *prefixes: Top-level package names to isolate (e.g. ``"PyQt6"``).
        extra: Exact module names to isolate as well (e.g. a single submodule
            that must be re-executed against the swapped-in dependencies).
    """
    tracked = set(extra)

    def _is_tracked(name):
        return name.split(".")[0] in prefixes or name in tracked

    saved = {name: mod for name, mod in sys.modules.items() if _is_tracked(name)}

    # ``sys.modules`` is not the whole story: importing ``a.b`` also binds ``b``
    # as an attribute of package ``a``. Restoring only ``sys.modules`` therefore
    # leaves the two identities disagreeing whenever a tracked module's parent
    # SURVIVES the block — ``Ankimon.pyobj.InfoLogger`` the attribute would keep
    # pointing at the module re-imported in here while
    # ``sys.modules["Ankimon.pyobj.InfoLogger"]`` points at the original. Which
    # one a later test sees depends on whether it writes ``from ..pyobj import
    # InfoLogger`` (attribute) or ``import Ankimon.pyobj.InfoLogger``
    # (sys.modules), and that is precisely the order-dependent breakage this
    # helper exists to prevent.
    #
    # A tracked parent needs none of this: it is restored as a whole object and
    # brings its own attributes back with it. Parents are re-resolved by name on
    # the way out so a parent that was itself swapped mid-block (conftest's
    # ``restore_package_stubs`` rebuilds the Ankimon stubs) is not written to
    # through a stale reference.
    saved_attrs = []
    for name in set(saved) | tracked:
        parent_name, _, attr = name.rpartition(".")
        if not parent_name or _is_tracked(parent_name):
            continue
        parent = sys.modules.get(parent_name)
        # A parent that isn't imported yet still gets an entry: importing the
        # child in here imports the parent too and binds the attribute, and the
        # parent — being untracked — then survives the block carrying a name
        # bound to a module that is no longer in sys.modules. Recording
        # _MISSING makes the exit path delete it.
        previous = _MISSING if parent is None else getattr(parent, attr, _MISSING)
        saved_attrs.append((parent_name, attr, previous))

    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in [n for n in list(sys.modules) if _is_tracked(n) and n not in saved]:
            del sys.modules[name]
        sys.modules.update(saved)
        for parent_name, attr, previous in saved_attrs:
            parent = sys.modules.get(parent_name)
            if parent is None:
                continue
            if previous is _MISSING:
                # The attribute did not exist before; an import in here created
                # it. Leaving it behind is the same leak in reverse.
                try:
                    delattr(parent, attr)
                except AttributeError:
                    pass
            else:
                setattr(parent, attr, previous)
