"""Tier-2 play probe: actually PLAY the real add-on headless (real windows).

Boots the genuine Ankimon, then answers cards by firing real Anki hooks and
catches/defeats via the real reviewer shortcuts. Asserts the real loop produces
the right events with no errors — proving the real windows survive a play session
offscreen. Run after sourcing the Tier-2 env file:

    python -m harness.checks.probe_real_play
"""

import sys
import pathlib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.real_driver import RealDriver


def main(seed=7) -> int:
    # Deterministic by default: real play is otherwise unseeded, so an unlucky
    # encounter RNG path can hit a real (rare) game bug — e.g. "Cannot choose from
    # an empty sequence" — and flake this gate. Seed it (mirrors smoke_play / #501);
    # pass seed=None to fuzz. (Such bugs are hunted deterministically by mega_fuzz.)
    if seed is not None:
        import random
        random.seed(seed)
    d = RealDriver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 0,
        "audio.sounds": False,
        "audio.sound_effects": False,
    })

    events = []
    resolutions = caught = defeated = 0

    st0 = d.get_state()
    print(f"start: {st0['main']['name']} Lv{st0['main']['level']} vs "
          f"{st0['enemy']['name']} Lv{st0['enemy']['level']} "
          f"(HP {st0['enemy']['hp']}/{st0['enemy']['max_hp']})")

    for i in range(80):
        events += d.answer("good")
        st = d.get_state()
        for who in ("main", "enemy"):
            p = st[who]
            assert 0 <= p["hp"] <= p["max_hp"], f"{who} HP out of range: {p}"
        if st["enemy"]["hp"] == 0:
            if resolutions % 2 == 0:
                events += d.catch(); caught += 1
            else:
                events += d.defeat(); defeated += 1
            resolutions += 1
            if resolutions >= 3:
                break

    kinds = Counter(e["type"] for e in events)
    errors = [e for e in events if e["type"] == "error"]
    final = d.get_state()

    print(f"answers ~{i + 1}, resolutions {resolutions} (caught {caught}, defeated {defeated})")
    print("event counts:", dict(kinds))
    print(f"collection: {final['collection']['count']} ids={final['collection']['ids']}")
    print(f"trainer Lv{final['trainer']['level']} xp={final['trainer']['xp']}")
    for e in errors[:5]:
        print("  ERROR:", e.get("message"), "|", e.get("exception"))

    assert not errors, f"{len(errors)} error event(s) during REAL play"
    assert kinds.get("battle", 0) > 0, "no battle turns"
    assert kinds.get("encounter", 0) > 0, "no encounters"
    print("probe_real_play: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
