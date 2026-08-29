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


def _create_css_source():
    """The other half of the HUD stylesheet, built by create_css_for_reviewer."""
    path = (
        Path(__file__).parent.parent
        / "src"
        / "Ankimon"
        / "functions"
        / "create_css_for_reviewer.py"
    )
    return path.read_text(encoding="utf-8")


# A selector that leads with any of these needs an element OUTSIDE the HUD's
# shadow root to match. The stylesheet is injected into a closed shadow root
# whose host is appended to <html>, so such a selector is doubly unreachable:
# it cannot cross the shadow boundary, and <body> is not even an ancestor of
# the host. Anki's theme reaches the HUD as a class on #ankimon-hud instead.
_ANCESTOR_THEME_PREFIXES = (
    ".night_mode ",
    ".theme-dark ",
    "html.",
    "body.",
    "html[",
    "body[",
)


def _selector_lines(css):
    for line in css.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("/*", "*", "@")):
            continue
        # selector lines either open a block or continue a comma-separated list
        if stripped.endswith("{") or stripped.endswith(","):
            yield stripped


def test_no_rule_depends_on_an_ancestor_outside_the_shadow_root():
    for source_name, css in (
        ("reviewer_obj.py", _hud_css_literal()),
        ("create_css_for_reviewer.py", _create_css_source()),
    ):
        for selector in _selector_lines(css):
            for prefix in _ANCESTOR_THEME_PREFIXES:
                assert not selector.startswith(prefix), (
                    f"{source_name}: selector {selector!r} matches on an ancestor "
                    "outside the HUD's shadow root, so it can never apply. "
                    "Anchor it on #ankimon-hud (e.g. '#ankimon-hud.night_mode')."
                )


def test_hud_markup_carries_ankis_theme_as_a_class():
    """The night-mode class must be on #ankimon-hud itself — that is the only
    theme signal that survives the shadow boundary."""
    source = REVIEWER_OBJ.read_text(encoding="utf-8")
    assert '<div id="ankimon-hud" class="night_mode">' in source, (
        "the HUD no longer emits Anki's theme as a class on #ankimon-hud"
    )
    assert "#ankimon-hud.night_mode" in _hud_css_literal(), (
        "no rule consumes the night_mode class, so dark mode styles nothing"
    )


def test_xp_text_is_not_overridden_by_the_display_pill_group():
    """#xp_text has its own cyan-on-black pill in create_css_for_reviewer.
    Listing it in reviewer_obj's later display-pill rules silently overrode
    that (equal specificity, later wins), which is what made the colour fix
    in #795 invisible."""
    for selector in _selector_lines(_hud_css_literal()):
        assert "#xp_text" not in selector, (
            f"selector {selector!r} re-styles #xp_text after "
            "create_css_for_reviewer already did; the later rule wins and the "
            "dedicated XP styling is lost"
        )
