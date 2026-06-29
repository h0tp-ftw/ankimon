"""
harness/bootstrap.py — make the aqt-free Ankimon core importable headless.

It does two things, in order:

1. Optionally point Ankimon's writable data dir at a throwaway location by
   setting ``ANKIMON_USER_PATH`` (read by ``resources.py`` at import time).
2. Put ``<repo>/src`` on ``sys.path`` and install a *stub* top-level ``Ankimon``
   package into ``sys.modules`` whose ``__path__`` points at the real source —
   WITHOUT executing ``Ankimon/__init__.py`` (which imports ``aqt`` and would
   fail / try to boot the whole GUI add-on).

After ``bootstrap()`` runs, ``import Ankimon.functions.encounter_functions``
(and every other aqt-free module) works, and their intra-package relative
imports resolve against the stub's ``__path__``. GUI modules that import
PyQt6/aqt will still fail to import — that is intentional; the harness must
never need them.

Call ``bootstrap()`` BEFORE importing anything from ``Ankimon``.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import types
from pathlib import Path


@contextlib.contextmanager
def quiet():
    """Suppress stdout for the duration of the block.

    The bundled poke_engine prints battle debug to stdout, and ``Settings`` prints
    on every config save. That noise is harmless for scenarios but would corrupt
    the JSON-line REPL protocol, so the driver runs game/settings calls in here.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


def bootstrap(user_path=None) -> None:
    """Prepare ``sys.path``/``sys.modules`` so the aqt-free core can be imported.

    ``user_path`` (str or Path), if given, becomes ``ANKIMON_USER_PATH`` so the
    DB and any writable files land there instead of the real profile. Set it to
    a fresh temp dir for an isolated session.
    """
    if user_path is not None:
        os.environ["ANKIMON_USER_PATH"] = str(user_path)

    src = str(SRC)
    if src not in sys.path:
        sys.path.insert(0, src)

    existing = sys.modules.get("Ankimon")
    if existing is None:
        pkg = types.ModuleType("Ankimon")
        pkg.__path__ = [str(SRC / "Ankimon")]
        pkg.__package__ = "Ankimon"
        # Mark it as a harness stub for easy identification.
        pkg.__ankimon_harness_stub__ = True
        sys.modules["Ankimon"] = pkg
    else:
        # Adopt an existing stub. Only a stub can be here — the real package
        # can't import in a headless interpreter (its __init__ needs aqt) — e.g.
        # tests/conftest.py installs a namespace-style stub during collection.
        # Make sure its __path__ resolves to the source tree.
        if not getattr(existing, "__path__", None):
            existing.__path__ = [str(SRC / "Ankimon")]


def repo_root() -> Path:
    return REPO_ROOT
