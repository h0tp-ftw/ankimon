"""Structural guard for the reviewer HUD's inline stylesheet.

The HUD stylesheet is a triple-quoted literal in ``reviewer_obj.py`` handed to a
``<style>`` element inside the HUD's shadow root. A malformed rule in there
raises nothing, breaks no import and trips no linter: the browser just drops
what it cannot parse. An unclosed block is worse than that — it swallows every
rule written after it. That is how a stray ``.night_mode #xp_text {`` came to sit
above ``.night_mode #ankimon-hud #xp_text {`` with only one closing brace between
them, which nothing in the suite noticed. These read the literal and check its
shape.
"""

import re
from pathlib import Path

REVIEWER_OBJ = (
    Path(__file__).parent.parent / "src" / "Ankimon" / "pyobj" / "reviewer_obj.py"
)


def _hud_css_literal():
    source = REVIEWER_OBJ.read_text(encoding="utf-8")
    match = re.search(r'hud_css \+= """(.*?)"""', source, re.S)
    assert match, "could not find the hud_css literal in reviewer_obj.py"
    return match.group(1)


def test_hud_css_braces_are_balanced():
    css = _hud_css_literal()
    assert css.count("{") == css.count("}"), (
        "unbalanced braces in the HUD stylesheet — an unclosed rule silently "
        "swallows every rule that follows it"
    )


def test_no_selector_opens_a_block_it_never_fills():
    """Two selector lines stacked back to back mean the first block is never
    closed (or, worse, parses as CSS nesting and quietly matches nothing).
    An at-rule such as ``@media`` legitimately wraps a selector, so it is the
    one opener allowed to be followed by another."""
    lines = [line.strip() for line in _hud_css_literal().splitlines() if line.strip()]
    for first, second in zip(lines, lines[1:]):
        if not first.endswith("{") or first.startswith("@"):
            continue
        assert not second.endswith("{"), (
            f"selector {first!r} opens a block whose next line {second!r} "
            "opens another — the first block is never filled or closed"
        )
