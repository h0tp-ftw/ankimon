"""The Ankidex briefing badge must gate on the same state the wild roll does.

``build_encounterable_ids`` only answers the progression half of the question.
The other two roll guards are resolved in the SPA, and both were wrong:

* **Guard 4 remap.** ``_meets_prerequisites`` maps a form id (>= 10000) to its
  base species before looking up ``PREREQUISITES``. ``renderBriefing`` looked the
  raw actual_id up instead, got ``undefined`` for 31 form ids, and fell through
  to the "no requirements" branch — rendering "Unlocked / No requirements" for
  targets the roll was actively blocking.
* **The collection used.** The roll gates on currently-owned Pokemon only
  (``load_collected_pokemon_ids`` -> ``captured_pokemon``). The payload's
  ``owned`` is wider: it folds in released Pokemon and ``pokemon_history``. So
  releasing a Mew marked Mewtwo's requirement met here while the roll went on
  refusing to produce it.

Extracts the two helpers out of ankidex.js and runs them under node, the same
way tests/test_battle_webview_index_html.py exercises the battle webview.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ANKIDEX_JS = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "Ankimon"
    / "ankidex"
    / "ankidex.js"
)

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node.js is not available")

RESOLVE_START = "function resolveActualId(id) {"
OWNS_START = "function ownsForRequirement(id) {"
PREREQS_START = "function prerequisitesFor(id, speciesId) {"
NODE_STATE_START = "function getNodeState(id) {"
FN_END = "\n}"


@pytest.fixture(scope="module")
def js_source():
    return ANKIDEX_JS.read_text(encoding="utf-8")


def _extract(source, start_marker):
    start = source.index(start_marker)
    end = source.index(FN_END, start) + len(FN_END)
    return source[start:end]


def _helpers(source):
    return "\n".join(
        _extract(source, marker)
        for marker in (RESOLVE_START, OWNS_START, PREREQS_START)
    )


def run_node_json(script):
    result = subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, f"node script failed:\n{result.stderr}"
    return json.loads(result.stdout.strip())


def _eval(source, state, expression):
    script = f"""
    const state = {json.dumps(state)};
    state.collection.owned = new Set(state.collection.owned);
    state.collection.ownedNow = new Set(state.collection.ownedNow);
    {_helpers(source)}
    console.log(JSON.stringify({expression}));
    """
    return run_node_json(script)


def _state(owned, owned_now, prerequisites=None, species=None):
    return {
        "collection": {"owned": owned, "ownedNow": owned_now},
        "prerequisites": prerequisites or {},
        "fullSpeciesMap": species or {},
    }


def _eval_node_state(source, state, node_id):
    """Run the real getNodeState() against a state, helpers and all."""
    script = f"""
    const state = {json.dumps(state)};
    state.collection.owned = new Set(state.collection.owned);
    state.collection.ownedNow = new Set(state.collection.ownedNow);
    {_helpers(source)}
    {_extract(source, NODE_STATE_START)}
    console.log(JSON.stringify(getNodeState({node_id})));
    """
    return run_node_json(script)


# --- the collection a requirement check reads --------------------------------


def test_requirement_check_ignores_released_pokemon(js_source):
    # Mew was caught and then released: present in `owned` (history), absent from
    # `ownedNow`. The roll's _meets_prerequisites sees an empty collection, so
    # Mewtwo must NOT read as requirement-met.
    state = _state(owned=[151], owned_now=[])
    assert _eval(js_source, state, "ownsForRequirement(151)") is False


def test_requirement_check_accepts_a_currently_owned_pokemon(js_source):
    state = _state(owned=[151], owned_now=[151])
    assert _eval(js_source, state, "ownsForRequirement(151)") is True


def test_requirement_check_applies_the_zygarde_id_alias(js_source):
    # resolveActualId(718) -> 10119; the collection stores the form id.
    state = _state(owned=[], owned_now=[10119])
    assert _eval(js_source, state, "ownsForRequirement(718)") is True


# --- Guard 4's species remap -------------------------------------------------


def test_form_id_inherits_its_base_species_prerequisites(js_source):
    # 10119 (Zygarde-Complete) carries no PREREQUISITES row; species 718 does.
    state = _state(owned=[], owned_now=[], prerequisites={"718": [716, 717]})
    assert _eval(js_source, state, "prerequisitesFor(10119, 718)") == [716, 717]


def test_an_id_with_its_own_row_does_not_consult_its_species(js_source):
    state = _state(owned=[], owned_now=[], prerequisites={"150": [151], "10043": [999]})
    assert _eval(js_source, state, "prerequisitesFor(10043, 150)") == [999]


def test_a_base_species_with_no_row_stays_requirement_free(js_source):
    state = _state(owned=[], owned_now=[], prerequisites={"150": [151]})
    assert _eval(js_source, state, "prerequisitesFor(25, 25) === undefined") is True


def test_the_remap_is_gated_on_the_form_id_range_the_roll_uses(js_source):
    # _meets_prerequisites only remaps for pokemon_id >= 10000. A base-range id
    # whose species_id happens to differ must NOT inherit, or the badge would
    # gate on a chain the roll never consults.
    state = _state(owned=[], owned_now=[], prerequisites={"150": [151]})
    assert _eval(js_source, state, "prerequisitesFor(9999, 150) === undefined") is True
    assert _eval(js_source, state, "prerequisitesFor(10000, 150)") == [151]


def test_a_form_whose_species_has_no_row_stays_requirement_free(js_source):
    state = _state(owned=[], owned_now=[], prerequisites={"150": [151]})
    assert _eval(js_source, state, "prerequisitesFor(10186, 3) === undefined") is True


# --- the badge path must actually use them -----------------------------------


def test_briefing_reads_requirements_through_the_remap(js_source):
    # Guards the wiring, not just the helper: a revert to the raw lookup would
    # silently reopen the 31-form hole.
    assert "let reqs = prerequisitesFor(id, p.species_id) || [];" in js_source
    assert "let reqs = state.prerequisites[id] || [];" not in js_source


def test_briefing_counts_requirements_against_the_roll_owned_set(js_source):
    assert "if (ownsForRequirement(reqId)) caughtCount++;" in js_source
    assert (
        "if (state.collection.owned.has(resolveActualId(reqId))) caughtCount++;"
        not in js_source
    )


def test_badge_does_not_promise_an_immediate_encounter(js_source):
    # The rare tiers carry weight 0 until 40% of the day's review goal is done,
    # so "ready for encounter" was false for the whole first stretch of every
    # day. The badge reports unlock state instead.
    assert "ready for encounter" not in js_source
    assert 'badgeEl.textContent = "Available";' not in js_source
    assert 'badgeEl.textContent = "Unlocked";' in js_source


# --- the rest of the SPA has to use them too ---------------------------------
# The badge was only one of the places that answers "is this requirement met?".
# Leaving the others on the wide set made the same panel contradict itself: the
# briefing counter said "0 of 1 requirements caught" from ownedNow while the row
# underneath it labelled that same Pokemon "Caught" from owned.


def test_the_briefing_requirement_rows_use_the_roll_owned_set(js_source):
    assert "      const rOwned = ownsForRequirement(reqId);" in js_source
    assert "const rOwned = state.collection.owned.has(reqId);" not in js_source


def test_the_prerequisite_strip_uses_the_roll_owned_set(js_source):
    # renderRequirements' Mega/Gmax branch (reqs = [p.species_id]) IS the roll's
    # Guard 3, which reads load_collected_pokemon_ids() and nothing wider.
    assert "    const isCaught = ownsForRequirement(reqId);" in js_source
    assert (
        "const isCaught = state.collection.owned.has(resolveActualId(reqId));"
        not in js_source
    )


def test_the_prerequisite_strip_consults_the_id_own_row_first(js_source):
    # Species-first lookup showed Giratina-Origin (10007 -> {487}) its species'
    # chain {483, 484} — Dialga + Palkia, where the roll gates on Giratina.
    assert "reqs = prerequisitesFor(p.actual_id, p.species_id);" in js_source
    assert (
        "state.prerequisites[p.species_id] || state.prerequisites[p.actual_id]"
        not in js_source
    )


def test_the_map_edge_split_keeps_completion_on_the_wide_set(js_source):
    # line-met is a requirement verdict (narrow); line-completed says the target
    # was captured, which is a collection state (wide).
    assert "const srcCaught = ownsForRequirement(reqId);" in js_source
    assert (
        "const dstCaught = state.collection.owned.has(resolveActualId(targetId));"
        in js_source
    )


def test_a_release_changes_the_collection_hash(js_source):
    """The hash is what gates every re-render, and a release moves an id out of
    ownedNow ALONE — `owned` keeps it (pokemon_history), and `seen` is computed
    as seen-minus-owned so it does not move either. Blind to ownedNow the hash
    came back identical, and the discovery map went on painting requirement
    states the roll no longer agreed with."""
    assert "${ownedNowList.length}" in js_source
    assert '${ownedNowList.slice(0, 10).join(",")}' in js_source


# --- the discovery map's node states -----------------------------------------


def test_a_released_requirement_does_not_unlock_a_map_node(js_source):
    # Mew caught then released: in `owned` (history), out of `ownedNow`. The
    # roll's _meets_prerequisites(150, {}) is False, so Mewtwo's node is locked.
    state = _state(owned=[151], owned_now=[], prerequisites={"150": [151]})
    assert _eval_node_state(js_source, state, 150) == "locked"


def test_a_currently_owned_requirement_unlocks_a_map_node(js_source):
    state = _state(owned=[151], owned_now=[151], prerequisites={"150": [151]})
    assert _eval_node_state(js_source, state, 150) == "available"


def test_a_released_requirement_does_not_satisfy_an_or_node(js_source):
    state = _state(
        owned=[144, 145],
        owned_now=[],
        prerequisites={"249": ["OR", [144, 145]]},
    )
    assert _eval_node_state(js_source, state, 249) == "locked"


def test_a_partly_met_chain_still_reads_in_progress(js_source):
    state = _state(
        owned=[144, 145, 146],
        owned_now=[144],
        prerequisites={"249": [144, 145, 146]},
    )
    assert _eval_node_state(js_source, state, 249) == "in-progress"


def test_a_map_node_form_id_inherits_its_species_requirements(js_source):
    # Without the remap a form id with no row of its own fell through to
    # "available" — the map's version of the 31-form hole.
    state = _state(
        owned=[],
        owned_now=[],
        prerequisites={"718": [716, 717]},
        species={"10119": {"species_id": 718}},
    )
    assert _eval_node_state(js_source, state, 10119) == "locked"


def test_a_caught_map_node_still_reads_from_the_wide_set(js_source):
    # "caught" is a collection state, not a requirement verdict: an evolved-away
    # or released Pokemon is still one the player has completed, exactly as the
    # briefing panel's "Completed" badge treats it.
    state = _state(owned=[150], owned_now=[], prerequisites={"150": [151]})
    assert _eval_node_state(js_source, state, 150) == "caught"
