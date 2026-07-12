---
name: validate-pr
description: >-
  Validate an Ankimon pull request end-to-end with NO human testing — check out the
  branch, ACTUALLY RUN it (the harness gate + diff-relevant scenarios + a targeted
  repro + profiling), do a static code review, and post a clear MERGE / DON'T-MERGE
  verdict with evidence. Use when asked to validate / review / test / sign off on / be
  the tester for a PR.
argument-hint: "[pr-number]"
arguments: [pr]
allowed-tools: Bash, Read, Grep, Glob, Skill
---

# Validate PR #$ARGUMENTS (no human in the loop)

Decide whether **PR #$ARGUMENTS** is safe to merge by **actually running the code**, not
just reading the diff. This *runs real tests* via the headless harness; the output is a
verdict a human can trust without re-testing.

> Run this on a machine with a normal GUI stack (x86 + Qt/WebEngine) for FULL coverage.
> On a headless box you still get Tier-1 (logic/state/data/perf — where ~all bugs live);
> just be explicit about what you couldn't run (WebEngine windows / visuals).

## 1. Check it out (isolated; never touch the main working tree)
```bash
gh pr view $ARGUMENTS --json title,headRefName,baseRefName,additions,deletions,files,body
git fetch origin main "pull/$ARGUMENTS/head:pr-$ARGUMENTS"
git worktree add /tmp/pr-$ARGUMENTS "pr-$ARGUMENTS"
cd /tmp/pr-$ARGUMENTS && git submodule update --init --recursive
```

## 1.5 Also build the merged-with-main state (do NOT skip this)
`mergeable: true` on GitHub only means the branch applies without conflict *markers* — it
says nothing about whether the combined result actually behaves. This repo's main-branch
ruleset does **not** require a PR to be rebased onto the latest `main` before merging
(`strict_required_status_checks_policy` is off), and the full test suite (`run_integrity_tests`)
only runs on `pull_request`, never on `push: main` — so a semantic break (two independently-clean
changes that combine into something broken, with zero textual conflict) can land undetected
until something notices it's broken *after* merge. Catch it before merge instead: build the
state this PR would actually produce and test that too, not just the branch in isolation.
```bash
git worktree add /tmp/pr-$ARGUMENTS-merged origin/main
cd /tmp/pr-$ARGUMENTS-merged && git submodule update --init --recursive
git merge --squash pr-$ARGUMENTS && git commit -m "sim: PR #$ARGUMENTS merged onto main, for validation only"
```
(If the merge conflicts here, that's real — resolve it as part of the rebase you'll need
before this PR can land, and note it in the verdict. Skip this step only when the PR's base
already *is* the current `main` tip, i.e. there's no drift to test.)

## 2. Diff → test plan
`git diff --stat <base>...HEAD`. Map the changed files to what to run:
- core / battle_loop / encounter / pyobj / db / settings → the **gate** + matching scenarios
- a claimed bug fix → **construct the exact repro** that should now pass
- perf-sensitive → **profile** and compare
- a real Qt/WebEngine window → Tier 2 (needs a GUI machine; if headless, flag it)
- new setting / input → **fuzz** it

## 2.5 Check the existing CI (do NOT skip — this is the easy miss)
```bash
gh pr checks $ARGUMENTS
```
If any check is **failing**, dig in before judging: `gh run view <run-id> --log-failed`
or `gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs`. **Red CI is part of the
verdict** — never say MERGE over a red check without explaining why it's a false negative.
Running green **locally is not the same as CI green** (deps/env differ — e.g. a module that
imports `requests`, or an RNG-flaky scenario). When in doubt, re-trigger and re-check.

## 3. ACTUALLY RUN IT — this is the real testing
Use the **`ankimon-harness`** skill for the how-to (its `reference.md` has every API).
Run this against **both** worktrees from steps 1 and 1.5 — the branch alone, then the
merged-with-main state. Minimum bar, always:
```bash
python3 harness/check.py            # the Tier-1 gate — must exit 0
```
Then, driven by the diff (examples):
```bash
python3 harness/scenarios/smoke_play.py        # play-through: no error events, invariants hold
python3 harness/scenarios/longrun.py 2000      # stress the loop
python3 harness/scenarios/auto_battle.py       # if auto-battle touched
```
- **Targeted repro** (the part that proves a fix): build the exact state and assert the new
  behavior — `Driver(seed={...})` + `set_enemy(...)`, then read the `battle` events; for a
  perf PR, `with profile(d, memory=True) as r: ...` and compare `r.as_dict()`.
- Every run: scan events for `type == "error"`; assert `get_state()` invariants (HP in
  `[0, max]`, caught-count/levels move as expected). An `error` event = a real crash.
- Tier 2 (GUI machine): `python3 harness/check.py` then the real-window probes / screenshots.
- **If the branch-only run passes but the merged-state run fails**, that's the signature of a
  semantic conflict — flag it explicitly in the verdict (not just "DON'T MERGE"), since the
  fix belongs in a rebase, not in either PR's own code being wrong.

## 4. Static review (catches what the harness can't reach)
Run the **`code-review`** skill on the diff for correctness bugs (logic the harness didn't exercise).

## 5. Verdict — post it
Write a short verdict and post it on the PR:
```bash
gh pr comment $ARGUMENTS --body-file /tmp/verdict-$ARGUMENTS.md
# (if gh pr comment errors, use: gh api -X POST repos/<owner>/<repo>/issues/$ARGUMENTS/comments -F body=@/tmp/verdict-$ARGUMENTS.md)
```
The verdict MUST contain:
- **GitHub CI status** (`gh pr checks`) — every check green, or which is red and why,
- **what you RAN** (exact commands) and the key results (event counts, numbers, any `error`),
- pass/fail per check,
- any bug found, with a **minimal repro** (the `seed`/`set_enemy` to reproduce it),
- a clear **MERGE** or **DON'T MERGE — reasons**,
- an honest **"couldn't verify here"** list (e.g. WebEngine windows / visuals on a headless
  box) — that's "needs a GUI run / human spot-check," NOT a silent pass.

## 6. Clean up
```bash
git worktree remove /tmp/pr-$ARGUMENTS --force; git branch -D "pr-$ARGUMENTS" 2>/dev/null
git worktree remove /tmp/pr-$ARGUMENTS-merged --force 2>/dev/null
```

## Honest scope
This genuinely tests logic / state / data / rewards / performance / regressions by running
the real game code — that's most of what matters for this add-on. It does **not** judge
pixels/CSS or felt latency on a user's machine; flag those for a human. Never edit `src/`
to make a check pass; report the bug instead.
