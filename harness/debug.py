"""
harness/debug.py — DEV-ONLY: attach a real debugger to a headless session.

Because the harness is a plain Python process driving the real game code, you can
set breakpoints in ``src/Ankimon`` and step through them while a *simulated* review
runs — no Anki, no clicking. Two ways:

1. pdb (stdlib, zero deps). Drop ``breakpoint()`` anywhere in a scenario or in
   ``src/Ankimon`` and run it, or run a scenario under pdb:
       python3 -m pdb harness/scenarios/smoke_play.py

2. debugpy (DAP — VS Code / PyCharm / any DAP client): set breakpoints in the editor,
   inspect/watch variables, step in/over/out. Either launch a scenario under debugpy:
       python3 -m debugpy --listen 5678 --wait-for-client harness/scenarios/smoke_play.py
   ...or call wait_for_client() inside your own script, then attach your editor:
       from harness.debug import wait_for_client
       from harness.driver import Driver
       wait_for_client()                 # blocks until the editor attaches on :5678
       d = Driver(settings_overrides={"battle.cards_per_round": 1})
       d.answer("good")                  # now step through battle_loop.py live

debugpy is optional — `pip install debugpy` into a venv (see harness/requirements-dev.txt).
pdb always works with zero deps.
"""

from __future__ import annotations


def wait_for_client(port: int = 5678, host: str = "127.0.0.1"):
    """Start a debugpy DAP server and block until a client (e.g. VS Code) attaches.

    Returns the ``debugpy`` module so you can call ``debugpy.breakpoint()`` later.
    """
    try:
        import debugpy
    except ImportError as e:
        raise RuntimeError(
            "debugpy is not installed — `pip install debugpy` (into a venv), or use "
            "the stdlib `breakpoint()` / `python3 -m pdb <scenario>` instead."
        ) from e
    debugpy.listen((host, port))
    print(f"debugpy: waiting for a debugger to attach on {host}:{port} ...")
    debugpy.wait_for_client()
    print("debugpy: attached — breakpoints in src/Ankimon are now live.")
    return debugpy
