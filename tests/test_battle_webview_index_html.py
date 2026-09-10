"""Tests for the security fixes applied to src/Ankimon/web/index.html.

The Pokemon battle web view is plain HTML/JS with no JavaScript test tooling
in this repository (no package.json, no Jest/Mocha/etc). To still exercise
the *runtime* behaviour of the changed code, these tests extract the exact
function/expression source that was modified straight out of the shipped
HTML file and execute it with Node.js (available on the system). This keeps
the tests tied to the real, deployed source instead of a hand-copied
reimplementation, so they fail if the fix is reverted or the source drifts.

Where the change is a plain statement (not an isolated function -- e.g. the
innerHTML -> textContent conversions), static source assertions are used
instead, mirroring how the rest of the diff's intent (never write untrusted
HTML into these sinks) can be verified without a full DOM.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML = (
    Path(__file__).resolve().parent.parent / "src" / "Ankimon" / "web" / "index.html"
)

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node.js is not available")

GET_URL_PARAMETER_START = "function getUrlParameter(name) {"
GET_URL_PARAMETER_END = (
    "return results === null ? '' : decodeURIComponent(results[1].replace(/\\+/g, ' '));\n\t\t}"
)

GENDER_TO_SYMBOL_START = "function genderToSymbol(gender) {"
GENDER_TO_SYMBOL_END = "return '';\n\t\t}"

SAFE_FONT_URL_PATTERN = re.compile(r"fontUrl\.replace\((/.+?/g), ''\)")


@pytest.fixture(scope="module")
def html_source():
    return INDEX_HTML.read_text(encoding="utf-8")


def _extract(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start) + len(end_marker)
    return source[start:end]


def run_node_json(script):
    """Run a node script whose last statement JSON.stringify()s its result
    to stdout, and return the decoded Python value.

    Decoding (rather than comparing raw JSON text) avoids false mismatches
    from Node's JSON.stringify not escaping non-ASCII characters (e.g. the
    gender symbols) the way Python's json.dumps does by default.
    """
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, f"node script failed:\n{result.stderr}"
    return json.loads(result.stdout.strip())


class TestGetUrlParameterBracketEscaping:
    """getUrlParameter's bracket-escaping regexes gained a /g flag in this PR."""

    def test_extracts_simple_parameter(self, html_source):
        fn = _extract(html_source, GET_URL_PARAMETER_START, GET_URL_PARAMETER_END)
        script = f"""
        global.location = {{ search: '?hp=50' }};
        {fn}
        console.log(JSON.stringify(getUrlParameter('hp')));
        """
        assert run_node_json(script) == "50"

    def test_missing_parameter_returns_empty_string(self, html_source):
        fn = _extract(html_source, GET_URL_PARAMETER_START, GET_URL_PARAMETER_END)
        script = f"""
        global.location = {{ search: '?hp=50' }};
        {fn}
        console.log(JSON.stringify(getUrlParameter('missing')));
        """
        assert run_node_json(script) == ""

    def test_parameter_name_with_repeated_brackets_is_escaped_globally(self, html_source):
        """Regression test for the /g flag fix.

        Before the fix, ``.replace(/[[]/, ...)`` / ``.replace(/[\\]]/, ...)``
        (without the global flag) only escaped the FIRST '[' and the FIRST
        ']' found in the parameter name. A name containing two bracket pairs
        (e.g. 'items[][]') left the second pair unescaped, corrupting the
        generated RegExp (a bare, unescaped '[]' is an empty character
        class that never matches) and making the parameter unreadable. With
        the /g flag every bracket is escaped, so lookups for such names
        succeed.
        """
        fn = _extract(html_source, GET_URL_PARAMETER_START, GET_URL_PARAMETER_END)
        script = f"""
        global.location = {{ search: '?items[][]=caught' }};
        {fn}
        console.log(JSON.stringify(getUrlParameter('items[][]')));
        """
        assert run_node_json(script) == "caught"

    def test_plus_signs_decoded_as_spaces(self, html_source):
        fn = _extract(html_source, GET_URL_PARAMETER_START, GET_URL_PARAMETER_END)
        script = f"""
        global.location = {{ search: '?nameTop=Mr.+Mime' }};
        {fn}
        console.log(JSON.stringify(getUrlParameter('nameTop')));
        """
        assert run_node_json(script) == "Mr. Mime"


class TestGenderToSymbol:
    """genderToSymbol lost its explicit 'N' branch in favour of a trailing
    default ``return '';`` covering every non 'F'/'M' value."""

    @pytest.mark.parametrize(
        "gender, expected",
        [
            ("F", "\u2640"),
            ("M", "\u2642"),
            ("N", ""),
            ("", ""),
            ("unexpected", ""),
        ],
    )
    def test_known_and_unknown_genders(self, html_source, gender, expected):
        fn = _extract(html_source, GENDER_TO_SYMBOL_START, GENDER_TO_SYMBOL_END)
        script = f"""
        {fn}
        console.log(JSON.stringify(genderToSymbol({json.dumps(gender)})));
        """
        assert run_node_json(script) == expected

    def test_undefined_gender_does_not_render_the_literal_string_undefined(self, html_source):
        """Regression test: calling with no argument must not make the
        function return `undefined`. The call sites concatenate the result
        straight onto the Pokemon's name (e.g. ``nameTop.textContent =
        name_Top + genderToSymbol(genderTop)``), so returning `undefined`
        would render names like 'Charizardundefined'.
        """
        fn = _extract(html_source, GENDER_TO_SYMBOL_START, GENDER_TO_SYMBOL_END)
        script = f"""
        {fn}
        console.log(JSON.stringify('Charizard' + genderToSymbol(undefined)));
        """
        assert run_node_json(script) == "Charizard"

    def test_null_gender_returns_empty_string(self, html_source):
        fn = _extract(html_source, GENDER_TO_SYMBOL_START, GENDER_TO_SYMBOL_END)
        script = f"""
        {fn}
        console.log(JSON.stringify(genderToSymbol(null)));
        """
        assert run_node_json(script) == ""


class TestFontUrlCssInjectionSanitization:
    """fontUrl is spliced into a <style> block's textContent, which is
    parsed as live CSS. This PR strips characters that could close the
    url('...') declaration / the CSS rule and inject arbitrary CSS."""

    def _extract_sanitize_regex_literal(self, html_source):
        match = SAFE_FONT_URL_PATTERN.search(html_source)
        assert match, "Could not find the safeFontUrl sanitization regex in index.html"
        return match.group(1)

    def test_strips_characters_that_could_break_out_of_the_css_rule(self, html_source):
        regex_literal = self._extract_sanitize_regex_literal(html_source)
        malicious = "x'); } body{background:url('javascript:alert(1)')} .y{content:\""
        script = f"""
        var pattern = {regex_literal};
        var input = {json.dumps(malicious)};
        console.log(JSON.stringify(input.replace(pattern, '')));
        """
        sanitized = run_node_json(script)
        for dangerous_char in ["'", '"', "(", ")", "\\", ";", "{", "}"]:
            assert dangerous_char not in sanitized, (
                f"dangerous character {dangerous_char!r} survived sanitization: {sanitized!r}"
            )

    def test_legitimate_forward_slash_path_is_unaffected(self, html_source):
        regex_literal = self._extract_sanitize_regex_literal(html_source)
        legitimate = "fonts/PokemonGB.ttf?v=1"
        script = f"""
        var pattern = {regex_literal};
        var input = {json.dumps(legitimate)};
        console.log(JSON.stringify(input.replace(pattern, '')));
        """
        sanitized = run_node_json(script)
        assert sanitized == legitimate

    def test_sanitized_value_cannot_close_the_url_or_font_face_rule(self, html_source):
        regex_literal = self._extract_sanitize_regex_literal(html_source)
        malicious = "a'); } * { color: red } @font-face { src: url('b"
        script = f"""
        var pattern = {regex_literal};
        var safeFontUrl = {json.dumps(malicious)}.replace(pattern, '');
        var css = "@font-face {{ font-family: 'pokemon'; src: url('" + safeFontUrl + "'); }}";
        console.log(JSON.stringify(css));
        """
        css = run_node_json(script)

        # Any leftover letters/words from the payload (e.g. the literal text
        # "@font-face") are harmless inert *content* inside the url('...')
        # string -- what actually matters is that none of the punctuation
        # needed to escape that string or the rule survived. Cross-check
        # against an independent Python re-implementation of the same
        # stripping rule applied to the exact template used in the source.
        expected_safe_font_url = re.sub(r"""['"()\\;{}]""", "", malicious)
        expected_css = (
            "@font-face { font-family: 'pokemon'; src: url('"
            + expected_safe_font_url
            + "'); }"
        )
        assert css == expected_css

        # Structural guarantee: exactly one font-face rule with a single,
        # well-formed url() declaration -- the malicious payload could not
        # inject extra braces, parens, or quotes into the stylesheet.
        assert css.count("{") == 1
        assert css.count("}") == 1
        assert css.count("(") == 1
        assert css.count(")") == 1
        # 2 quotes around 'pokemon' + 2 around the url('...') value.
        assert css.count("'") == 4


class TestNoInnerHtmlAssignmentsForDynamicContent:
    """Static regression checks guarding the innerHTML -> textContent XSS
    fix for the elements whose content is derived from user-controlled
    data (nicknames, traded Pokemon, battle text) or from a value written
    into a live <style> block."""

    @pytest.mark.parametrize(
        "identifier",
        [
            "level_bottom",
            "level_top",
            "nameTop",
            "nameBottom",
            "health",
            "battleText",
            "styleElement",
        ],
    )
    def test_uses_textcontent_not_innerhtml(self, html_source, identifier):
        assert re.search(rf"\b{identifier}\.textContent\s*=", html_source), (
            f"expected an assignment to {identifier}.textContent"
        )
        assert not re.search(rf"\b{identifier}\.innerHTML\s*=", html_source), (
            f"found a forbidden assignment to {identifier}.innerHTML"
        )

    def test_style_element_content_is_built_from_the_sanitized_variable(self, html_source):
        match = re.search(r'styleElement\.textContent\s*=.*;', html_source)
        assert match, "could not find the styleElement.textContent assignment"
        assignment = match.group(0)
        assert "safeFontUrl" in assignment
        # The raw, un-sanitized fontUrl variable must never be spliced
        # directly into the CSS (only the sanitized safeFontUrl may be).
        assert "fontUrl" not in assignment.replace("safeFontUrl", "")