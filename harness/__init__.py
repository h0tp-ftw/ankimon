"""
Ankimon agent harness (dev-only — NOT shipped with the addon).

This package lets an AI agent (or a plain test) *play* Ankimon headlessly: it
boots the aqt-free game core without Anki/PyQt6, drives the same callables the
GUI buttons/hooks invoke, and reads back a structured event stream so the agent
can observe exactly what happened — the machine-readable equivalent of watching
the screen.

Modules:
    bootstrap   make the aqt-free Ankimon core importable under plain python3
    fakes       recording stand-ins for the GUI windows (test_window/evo/pc/...)
    headless_env build the core game state + services for a fresh session
    driver      high-level agent actions (answer/catch/defeat/encounter/...)
    server      a JSON-line REPL so an agent can drive a live session
    scenarios/  example scripted play sessions
    checks/     import-safety + smoke probes
"""
