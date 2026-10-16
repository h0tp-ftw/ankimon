"""Stored-XSS regressions for the Profile, Team, and Mobile History screens.

These pages consume values that can originate in an imported SQLite save.  Keep
the tests close to the JavaScript sinks so a future template refactor cannot
quietly reintroduce raw attribute interpolation or ``innerHTML`` rendering.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILE_JS = ROOT / "src" / "Ankimon" / "ankimon_profile_web" / "profile.js"
TEAM_JS = ROOT / "src" / "Ankimon" / "ankimon_profile_web" / "team.js"
HISTORY_JS = ROOT / "src" / "Ankimon" / "ankimon_mobile_web" / "history.js"
NODE = shutil.which("node")


@pytest.fixture(params=[PROFILE_JS, TEAM_JS], ids=["profile", "team"])
def profile_team_source(request):
    return request.param.read_text(encoding="utf-8")


def _function_block(source: str, name: str, next_name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index(f"function {next_name}(", start)
    return source[start:end]


def test_profile_and_team_escape_text_and_quoted_attributes(profile_team_source):
    """Quotes must be escaped too: text-node serialisation alone is unsafe in
    ``src``, ``alt``, and ``data-*`` attributes built with template strings.
    """

    helper = _function_block(profile_team_source, "esc", "num")

    assert re.search(r"replace\(/\[&<>\"'\]/g", helper)
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in helper
    assert "document.createElement" not in helper
    assert ".innerHTML" not in helper


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the JavaScript proof")
@pytest.mark.parametrize("path", [PROFILE_JS, TEAM_JS], ids=["profile", "team"])
def test_shipped_escape_helper_blocks_attribute_breakout(path):
    """Execute the exact shipped helper against a quote-breakout payload."""

    source = path.read_text(encoding="utf-8")
    helper = _function_block(source, "esc", "num").strip()
    payload = "\"><img src=x onerror=globalThis.pwned=1 data-test='"
    expected = "&quot;&gt;&lt;img src=x onerror=globalThis.pwned=1 data-test=&#39;"
    probe = """
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const esc = vm.runInNewContext(`(${input.helper})`);
process.stdout.write(esc(input.payload));
"""
    result = subprocess.run(
        [NODE, "-e", probe],
        input=json.dumps({"helper": helper, "payload": payload}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == expected


def test_profile_escapes_every_dynamic_image_path():
    source = PROFILE_JS.read_text(encoding="utf-8")

    assert 'src="${esc(data.sprite_url)}"' in source
    assert 'src="${esc(stub.sprite)}"' in source
    assert source.count('src="${esc(m.sprite || pkmnSprite(m))}"') == 2
    assert 'src="${BADGE_BASE}/${esc(b.id)}.png"' in source
    assert 'src="${esc(s.url)}"' in source

    assert 'src="${m.sprite || pkmnSprite(m)}"' not in source
    assert 'src="${BADGE_BASE}/${b.id}.png"' not in source

    assert source.count('alt="${esc(data.name)}"') == 1
    assert source.count('alt="${esc(m.n)}"') == 2
    assert source.count('alt="${esc(b.name)}"') == 1
    assert source.count('alt="${esc(s.label)}"') == 1
    assert 'data-val="${esc(val)}"' in source


def test_team_escapes_every_dynamic_image_path():
    source = TEAM_JS.read_text(encoding="utf-8")

    assert source.count('src="${esc(spriteUrl(m))}"') == 2
    assert source.count('src="${esc(spriteUrl(c))}"') == 1
    assert 'src="${spriteUrl(m)}"' not in source
    assert 'src="${spriteUrl(c)}"' not in source

    assert source.count('alt="${esc(m.n)}"') == 2
    assert source.count('alt="${esc(c.n)}"') == 1
    assert 'data-val="${esc(val)}"' in source


def test_history_builds_untrusted_rows_with_dom_text_nodes():
    source = HISTORY_JS.read_text(encoding="utf-8")
    render = _function_block(source, "renderHistory", "clearHistory")

    # No value from an imported history row may be reparsed as markup.
    assert ".innerHTML" not in render
    assert ".outerHTML" not in render
    assert "insertAdjacentHTML" not in render
    assert "makeElement" in render
    assert "appendText" in render
    assert "element.textContent = String(text)" in source

    # Class names are limited to the four known outcomes; nominally numeric DB
    # fields are coerced before display even though textContent is also safe.
    assert "Object.prototype.hasOwnProperty.call(OUTCOME_META, entry.outcome)" in render
    assert "finiteNumber(entry.companion_level, 5)" in render
    assert "finiteNumber(entry.enemy_level, 5)" in render
    for field in ("xp_gained", "trainer_xp_gained", "cash_gained"):
        assert f"entry.{field}" in render
    assert "positiveNumber(rawValue)" in render


def test_history_rejects_non_array_and_non_object_payload_shapes():
    source = HISTORY_JS.read_text(encoding="utf-8")
    render = _function_block(source, "renderHistory", "clearHistory")

    assert "Array.isArray(historyList) ? historyList : []" in render
    assert "if (!entry || typeof entry !== 'object') return;" in render


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the DOM proof")
def test_history_payloads_remain_text_and_numeric_fields_are_coerced():
    """Drive the shipped renderer with hostile imported database values.

    The tiny DOM fake deliberately has no HTML parser: if the renderer regresses
    to ``innerHTML`` the probe fails, while text-node rendering records the
    payload literally and creates no attacker-selected element.
    """

    probe = r"""
const fs = require('fs');
const vm = require('vm');

class FakeClassList {
    add() {}
    remove() {}
}

class FakeNode {
    constructor(tag, text = '') {
        this.tagName = tag.toUpperCase();
        this.className = '';
        this.children = [];
        this._text = String(text);
        this.classList = new FakeClassList();
    }
    set textContent(value) {
        this._text = String(value);
        this.children = [];
    }
    get textContent() {
        return this._text + this.children.map((child) => child.textContent).join('');
    }
    get childNodes() { return this.children; }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this.children = children; this._text = ''; }
}

const elements = {
    'history-empty': new FakeNode('div'),
    'history-list': new FakeNode('div'),
};
const document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeNode('#text', text),
    getElementById: (id) => elements[id] || null,
    body: new FakeNode('body'),
};
const context = {
    document,
    window: {},
    qt: { webChannelTransport: {} },
    QWebChannel: function () {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(0, 'utf8'), context);

context.renderHistory([
    {
        outcome: 'caught\" onclick=globalThis.outcomePwned=1',
        companion_name: '<img src=x onerror=globalThis.namePwned=1>',
        enemy_name: '<svg onload=globalThis.enemyPwned=1>',
        companion_level: '<img src=x onerror=globalThis.levelPwned=1>',
        enemy_level: '\"><script>globalThis.enemyLevelPwned=1</script>',
        xp_gained: '<img src=x onerror=globalThis.rewardPwned=1>',
        trainer_xp_gained: 'Infinity',
        cash_gained: '\"><svg onload=globalThis.cashPwned=1>',
        timestamp: '<img src=x onerror=globalThis.timePwned=1>',
    },
    {
        outcome: 'caught', companion_name: 'Pikachu', enemy_name: 'Eevee',
        companion_level: '7', enemy_level: 8,
        xp_gained: '10', trainer_xp_gained: 20, cash_gained: '30',
        timestamp: 1,
    },
]);

function walk(node, found = []) {
    found.push({ tag: node.tagName, className: node.className });
    node.children.forEach((child) => walk(child, found));
    return found;
}
const list = elements['history-list'];
process.stdout.write(JSON.stringify({
    nodes: walk(list),
    text: list.textContent,
    rows: list.children.length,
}));
"""
    result = subprocess.run(
        [NODE, "-e", probe],
        input=HISTORY_JS.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        check=True,
    )
    rendered = json.loads(result.stdout)

    assert rendered["rows"] == 2
    assert all(
        node["tag"] not in {"IMG", "SVG", "SCRIPT"} for node in rendered["nodes"]
    )
    assert all("outcomePwned" not in node["className"] for node in rendered["nodes"])
    classes = {node["className"] for node in rendered["nodes"]}
    assert "history-item outcome-caught" in classes
    assert "outcome-badge badge-caught" in classes
    assert "CAUGHT" in rendered["text"]
    assert "<img src=x onerror=globalThis.namePwned=1>" in rendered["text"]
    assert "<svg onload=globalThis.enemyPwned=1>" in rendered["text"]
    assert rendered["text"].count("Lv.5") == 2
    assert "rewardPwned" not in rendered["text"]
    assert "+10 XP" in rendered["text"]
    assert "+20 Trainer XP" in rendered["text"]
    assert "+30¥" in rendered["text"]
