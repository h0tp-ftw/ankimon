# PR #827: monthly challenge review fixes

The follow-up fixes the outstanding issues from the review of local commit
`9d4a03f0` and supplies the reclaim action missing from the branch. The earlier
DB-generation checks, fetch-only worker and automatic restoration of accepted
Pokemon remain in place.

## Decisions belong to one active session

`check_and_award_monthly_pokemon()` stores its pending request on `services`.
Another request for the same DB manager, generation and Anki collection is
coalesced throughout fetching, processing and modal presentation. A new session
can start immediately; an old callback can release only its own request.
A menu click during an automatic fetch upgrades the pending request to an explicit
reclaim/progress request, including offline feedback. Requests made inside an
existing dialog remain coalesced. Fetch, dispatch and processing failures release
the pending request for retry.

Profile connectivity callbacks capture the opening collection and discard
results after that collection closes or changes. After the decision dialog,
the monthly handler checks both session identity and the offered decision and
ownership. A stale prompt cannot overwrite a newer decision or trained Pokemon.

## Persistence precedes presentation

The collection helper reports whether the Pokemon save succeeded. UI refresh
errors do not turn a committed award into a failed save. Monthly awards defer
refresh until their accepted decision is saved, then revalidate the session
after refresh/error presentation before showing the acceptance notice.

A failed save preserves the prior decision: unclaimed stays `0`, accepted stays
`1` for automatic restoration, and an explicit reclaim failure stays rejected
(`2`) until the user retries. There is no failure-state write after an error
dialog. The Pokemon row and accepted challenge-ID/status are written in one SQLite
transaction, with rollback on either a statement or commit failure. Ownership
cache invalidation and Pokedex updates follow the successful commit; no database
transaction spans a modal dialog.

## Reclaim and sprite rendering

`Ankimon → Profile → Monthly Challenge` now opens the current challenge, including
a previously rejected reward. Ordinary startup checks continue to respect a
rejection. The explicit action does not clear that rejection before a successful
claim. If the Pokemon is already owned, the action reports its current level and
defeated count without replacing its progress or reinterpreting its decision.
Unavailable challenge data and missing rating eligibility produce feedback.
The rejection notice no longer promises a past-challenge browser.

Sprite dimensions are read before QMovie decodes its first frame, and scaling
preserves aspect ratio within the 120px and 64px labels. This also handles a
single-frame GIF: decoding into QMovie before setting its scale would cache an
unscaled first frame. Movies remain parented to their labels, and hiding sprites
still prevents loading them.

## Regression coverage

- `tests/test_monthly_challenge_fixes.py`: duplicate requests, nested requests,
  failure cleanup/retry, old callbacks completing after a newer request, stale
  decisions/ownership, save failure, and explicit reclaim. Existing session
  identity and accepted-restoration tests remain active.
- `tests/test_database_manager.py`: failed metadata writes and failed commits
  roll back both new and existing Pokemon for all three prior decision states;
  independent SQLite readers verify durability, and successful retries preserve
  the main-Pokemon flag and update the Pokedex.
- `tests/test_profile_hooks.py`: old/new profile connectivity completions and
  closed-profile callbacks.
- `harness/scenarios/monthly_challenge.py`: genuine offscreen Qt dialogs and
  SQLite, queued callbacks and actual button clicks. Covers duplicate requests,
  profile changes, a switch during a real error dialog after injected refresh
  failure, a committed award despite refresh failure, menu-driven rejection and
  reclaim while an automatic fetch is pending, atomic award failure and retry,
  a stale rejection after an external award, and sprite sizes/visibility.
  It uses disposable saves and controlled HTTP responses; unrelated audio
  constructors are substituted. Set `ANKIMON_MONTHLY_SCREENSHOTS` to a directory
  to save both sprite sizes.
- `tests/test_monthly_challenge_qt.py` runs the real-dialog scenarios in isolated
  child processes. The Tier-2 harness CI job also runs them directly.

Run `python3 harness/check.py`, `python -m pytest tests/`, and
`python -m harness.scenarios.monthly_challenge` in the Tier-2 environment. The
profile-hook/menu changes also require the normal real-Anki startup smoke test.

Validation on 2026-09-26: all nine Tier-1 checks passed; the full pytest suite
passed with 1,141 passed and 40 skipped; all eight real-dialog scenarios passed.
A real Anki 26.08.1 launch in a disposable profile reached completed Ankimon
startup and verified the new menu action after dismissing first-run onboarding.
Native Qt tests required execution outside the restricted sandbox. Both the
synthetic 143×24 GIF and the actual Wingull GIF fit at 120×20 and 64×10.
