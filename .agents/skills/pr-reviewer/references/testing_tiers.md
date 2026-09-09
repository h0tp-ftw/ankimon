# Authoring Focused Tier 1 and Tier 2 Proofs

Choose a behavior the PR claims to change. Construct its initial state, exercise the affected operation, and assert the observable result. Attach the actual code, command, exit status, and output to the review. The templates contain runnable samples; adapt them before using them as evidence for another claim.

## Tier 1: core logic and battle events

Use [tier1_proof_template.py](../templates/tier1_proof_template.py) for game logic without Anki or Qt. Install `requests` and initialize the poke-engine submodule first.

A driver action returns the events it produced and clears the queue. Accumulate the results instead of draining again:

```python
from harness.driver import Driver


def run_proof():
    d = Driver(
        seed={"main": {"species": "Pikachu", "level": 25}},
        settings_overrides={"battle.cards_per_round": 1},
        first_encounter=False,
    )
    events = d.set_enemy(species="Pidgey", level=10)
    events.extend(d.answer("good"))
    state = d.get_state()
    assert any(event["type"] == "battle" for event in events)
    assert not any(event["type"] == "error" for event in events)
    assert 0 <= state["main"]["hp"] <= state["main"]["max_hp"]
    return True
```

This proves that answering a card produces a battle with valid HP. A damage, leveling, or encounter-economy PR needs assertions for its specific numerical or state-transition claim as well. For random outcomes, control the relevant randomness and settings; a fixed number of answers does not guarantee a particular encounter or faint.

## Tier 2: real Qt interactions and persistence

Use [tier2_proof_template.py](../templates/tier2_proof_template.py). Set up the environment with `harness/setup_tier2.sh` and source `.tier2/env.sh`.

The sample boots a genuine `RealDriver`, inserts a collection Pokemon with `build_pokemon()` and `db.save_pokemon()`, opens its details, types a nickname with `QTest`, clicks the rename button, and verifies the SQLite nickname changed. It fails if the widgets are missing or persistence did not occur.

The existing interfaces are:

| Needed object | Access |
| --- | --- |
| QApplication | `d.env.app` |
| Database | `d.services.db` |
| PC window | `d.services.pokemon_pc` |
| Pokemon fixture | `harness.fixtures.build_pokemon(spec)` |

`RealDriver(seed=...)` is unsupported. Insert collection fixtures after boot using the APIs above. For a test of live battle state, configure that state too; writing only a main-Pokemon DB row does not update the existing live object.

Find widgets within the relevant window and use their actual labels. The PC search field also mentions "nickname", so the rename sample matches "Enter a new Nickname for your Pokémon". Assert that the input and action exist before interacting. The widget helper requires every supplied getter match.

After direct widget calls, process Qt events and read `d.drain_events()`. After driver actions such as `d.catch()` or `d.answer()`, inspect their returned events instead. An empty error list supplements a feature-specific assertion; it cannot prove that a UI action or save happened.

For settings changes, drive the actual settings control and check both the saved configuration and the loaded value. Inspect the current settings implementation instead of assuming a `SettingsDialog()` or a placeholder dropdown exists. For WebEngine behavior, request `webengine=True, require_webengine=True` and inspect the real DOM.

## Running and saving proofs

Run these commands from the repository root:

```bash
python .agents/skills/pr-reviewer/scripts/run_proof_scenario.py --init-tier1 tests/proofs/proof_pr_818_tier1.py
python .agents/skills/pr-reviewer/scripts/run_proof_scenario.py --file tests/proofs/proof_pr_818_tier1.py
```

Use the actual PR number, and `--init-tier2` for the Qt template. Scaffolding refuses overwrites. The copied templates locate the checkout from their ancestors or the working directory, so external copies must be run from the checkout.

The `--file` contract is a trusted Python module exposing `run_proof()` with an explicit `True` result after its assertions. A missing function, an exception, `SystemExit`, or any other return value fails the run. This helper is not a sandbox and does not execute pytest functions or `__main__` blocks; run those with their normal test runner.

Default the review report to NOT RUN. Only record PASSED after checking the actual exit status and relevant assertions; record blocked checks and their missing prerequisites.
