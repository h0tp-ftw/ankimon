def test_return_id_for_item_name_normalization():
    # Because of Anki imports in main `__init__`, we need to import pokedex_functions specifically
    from Ankimon.functions.pokedex_functions import return_id_for_item_name

    # Should resolve correctly despite casing/spacing/punctuation
    assert return_id_for_item_name("Dubious Disc") == "301"
    assert return_id_for_item_name("King's Rock") == "198"
    assert return_id_for_item_name("Up-Grade") == "229"


def test_normalize_item_identifier_folds_both_shapes():
    from Ankimon.functions.pokedex_functions import normalize_item_identifier

    # Display shape -> csv shape.
    assert normalize_item_identifier("King's Rock") == "kings-rock"
    assert normalize_item_identifier("  Dubious Disc  ") == "dubious-disc"
    # U+2019 (typographic apostrophe) folds the same as the ASCII one, which is
    # what lets the csv side meet the display side.
    assert normalize_item_identifier("Kofu’s Wallet") == "kofus-wallet"
    assert normalize_item_identifier("Kofu's Wallet") == "kofus-wallet"
    # Combining accents are stripped, so "Poke" reaches "poké".
    assert normalize_item_identifier("Poké Ball") == "poke-ball"
    assert normalize_item_identifier("Flabébé Pollen") == "flabebe-pollen"
    # Already-canonical identifiers are unchanged (the folding is idempotent).
    assert normalize_item_identifier("dubious-disc") == "dubious-disc"
    # Non-string input misses cleanly instead of raising.
    assert normalize_item_identifier(301) == ""
    assert normalize_item_identifier(None) == ""


def test_return_id_for_item_name_reaches_accented_identifiers():
    """The nine rows whose identifier carries U+2019 or an accent.

    Folding only the caller's side (as the original fix did) cannot match these,
    because the stored identifier is the odd one. Both sides are folded now.
    """
    from Ankimon.functions.pokedex_functions import return_id_for_item_name

    assert return_id_for_item_name("Poke Ball") == "4"
    assert return_id_for_item_name("Poké Ball") == "4"
    assert return_id_for_item_name("Leader's Crest") == "2046"
    assert return_id_for_item_name("Koraidon's Poke Ball") == "1667"
    assert return_id_for_item_name("Jalapeno") == "1756"
    # And the cases the original fix already handled keep working.
    assert return_id_for_item_name("Dubious Disc") == "301"


def test_utils_item_lookups_accept_display_names():
    """get_item_id/get_item_price did the same raw match and were left behind."""
    import Ankimon.utils as utils

    assert utils.get_item_id("Dubious Disc") == 301
    assert utils.get_item_id("King's Rock") == 198
    assert utils.get_item_id("Poké Ball") == 4
    assert utils.get_item_id("poke-ball") == 4

    # get_item_price returns the cost for the same folded lookup. potion is a
    # priced item; exp-share is priced 0 and is excluded from the shop by that.
    assert utils.get_item_price("Potion") == utils.get_item_price("potion")
    assert utils.get_item_price("Potion") > 0


def test_item_lookups_reject_names_and_rows_that_fold_to_empty(tmp_path):
    """A blank identifier cell must not answer for a blank/non-string name.

    Both sides fold, and both a blank name and a blank identifier fold to "",
    so a malformed row would otherwise match every degenerate lookup. The
    shipped items.csv has no such row — this pins the guard, not the data.
    """
    import Ankimon.utils as utils

    malformed = tmp_path / "items.csv"
    malformed.write_text(
        "id,identifier,category_id,cost,fling_power,fling_effect_id\n"
        "9999,,1,7777,,\n"
        "4,poke-ball,34,200,,\n",
        encoding="utf-8",
    )

    for bad in (None, "", "   ", 301):
        assert utils.get_item_price(bad, malformed) is None
        assert utils.get_item_id(bad, malformed) is None
    # A real row in the same file still resolves, so the guard rejects the
    # empty key rather than the whole file.
    assert utils.get_item_price("Poke Ball", malformed) == 200
    assert utils.get_item_id("Poke Ball", malformed) == 4


def test_bundled_lookups_miss_cleanly_on_empty_and_non_string_names():
    from Ankimon.functions.pokedex_functions import return_id_for_item_name
    import Ankimon.utils as utils

    for bad in (None, "", "   ", 301, "’"):
        assert return_id_for_item_name(bad) is None
        assert utils.get_item_price(bad) is None
        assert utils.get_item_id(bad) is None


def test_items_cost_index_keeps_the_first_row_for_a_folded_key():
    """items.csv repeats identifiers, and five repeats disagree on id/cost.

    The lookups scanned the file and returned the FIRST match; the index must
    answer the same. ``metronome`` is the sharp case: id 254/cost 4000 on its
    first row, id 20118/cost 1000 on the two later ones.
    """
    from Ankimon.functions.pokedex_functions import (
        load_items_cost_index,
        return_id_for_item_name,
    )
    import Ankimon.utils as utils

    assert load_items_cost_index()["metronome"]["id"] == "254"
    assert return_id_for_item_name("Metronome") == "254"
    assert utils.get_item_id("Metronome") == 254
    assert utils.get_item_price("Metronome") == 4000


def test_items_cost_index_answers_exactly_like_the_linear_scan():
    """Equivalence over the whole shipped file, not a sampled few.

    Guards the optimization itself: for every identifier in items.csv the index
    must return the same row the old first-match scan did, and no identifier may
    fold onto another's key.
    """
    import csv

    from Ankimon.functions.pokedex_functions import (
        load_items_cost_index,
        normalize_item_identifier,
        return_id_for_item_name,
    )
    from Ankimon.resources import csv_file_items_cost

    with open(csv_file_items_cost, mode="r", encoding="utf-8") as csvfile:
        rows = list(csv.DictReader(csvfile))

    first_match = {}
    for row in rows:
        first_match.setdefault(row["identifier"], row["id"])

    index = load_items_cost_index()
    for identifier, expected_id in first_match.items():
        assert index[normalize_item_identifier(identifier)]["id"] == expected_id
        assert return_id_for_item_name(identifier) == expected_id

    # No two distinct identifiers share a folded key, which is what makes
    # folding the stored side safe in the first place.
    assert len(index) == len({normalize_item_identifier(i) for i in first_match})
    assert len(index) == len(first_match)


def _reset_items_caches(monkeypatch, csv_path):
    """Point the item caches at ``csv_path`` and rebuild them from scratch.

    Both globals are module state that outlives a test, so they are set to None
    on the way in AND restored on the way out via monkeypatch's undo.
    """
    from Ankimon.functions import pokedex_functions as pf

    monkeypatch.setattr(pf, "csv_file_items_cost", csv_path)
    monkeypatch.setattr(pf, "_items_cost_cache", None)
    monkeypatch.setattr(pf, "_items_cost_index", None)
    return pf


def test_index_build_skips_rows_whose_identifier_folds_to_empty(monkeypatch, tmp_path):
    """The row-side half of the blank-identifier guard, on its own.

    ``load_items_cost_index`` must not key a blank identifier cell, or every
    lookup made with a blank name would find it.
    """
    malformed = tmp_path / "items.csv"
    malformed.write_text(
        "id,identifier,category_id,cost,fling_power,fling_effect_id\n"
        "9999,,1,7777,,\n"
        "9998,   ,1,6666,,\n"
        "4,poke-ball,34,200,,\n",
        encoding="utf-8",
    )
    pf = _reset_items_caches(monkeypatch, malformed)

    index = pf.load_items_cost_index()
    assert "" not in index
    assert set(index) == {"poke-ball"}
    assert pf.return_id_for_item_name(None) is None
    assert pf.return_id_for_item_name("") is None
    assert pf.return_id_for_item_name("Poke Ball") == "4"


def test_lookups_never_use_an_empty_folded_name_as_a_key(monkeypatch):
    """The caller-side half, tested independently of the row-side half.

    The two guards are deliberate defence in depth: even handed an index that
    somehow keys "", no lookup may ask for it. Injecting that index is the only
    way to exercise this half while the row-side guard is doing its job.
    """
    from Ankimon.functions import pokedex_functions as pf
    import Ankimon.utils as utils

    poisoned = {
        "": {"id": "9999", "cost": "7777"},
        "poke-ball": {"id": "4", "cost": "200"},
    }
    monkeypatch.setattr(pf, "_items_cost_index", poisoned)

    for bad in (None, "", "   ", 301, "’"):
        assert pf.return_id_for_item_name(bad) is None
        assert utils.get_item_price(bad) is None
        assert utils.get_item_id(bad) is None
    # The same index still answers a real name, so the guard rejects the empty
    # key rather than refusing to look anything up.
    assert pf.return_id_for_item_name("Poke Ball") == "4"
    assert utils.get_item_id("Poke Ball") == 4


def test_unreadable_items_csv_still_warns_and_falls_back(monkeypatch, tmp_path):
    """An empty index must not silently answer "miss" for an unreadable file.

    ``_load_items_cost_cache`` memoizes ``[]`` when items.csv cannot be read, so
    the index comes back empty. ``items_cost_index_for`` returns None for that,
    which sends both lookups down their own open() — the only thing that still
    reports the failure as the historical warning-plus-fallback (1000 / 4).
    """
    import Ankimon.utils as utils

    missing = tmp_path / "does-not-exist.csv"
    pf = _reset_items_caches(monkeypatch, missing)

    warnings = []
    monkeypatch.setattr(
        utils, "showWarning", lambda msg, *a, **kw: warnings.append(msg)
    )
    monkeypatch.setattr(
        utils,
        "show_warning_with_traceback",
        lambda *a, **kw: warnings.append("traceback"),
    )

    assert pf.load_items_cost_index() == {}
    assert pf.items_cost_index_for(missing) is None

    assert utils.get_item_price("Potion", missing) == 1000
    assert utils.get_item_id("Potion", missing) == 4
    assert len(warnings) == 2

    # A degenerate name takes the same route: it must not short-circuit past the
    # open() and turn an unreadable file into a silent miss.
    assert utils.get_item_price(None, missing) == 1000
    assert utils.get_item_id(None, missing) == 4
    assert len(warnings) == 4


def test_items_cost_index_for_refuses_a_path_that_is_not_the_bundled_file(tmp_path):
    """The index answers only for the file it was built from."""
    from Ankimon.functions.pokedex_functions import items_cost_index_for
    from Ankimon.resources import csv_file_items_cost

    assert items_cost_index_for(tmp_path / "items.csv") is None
    assert items_cost_index_for(str(csv_file_items_cost)) is not None


def test_capitalized_sprite_names_now_resolve_and_leave_the_mart():
    """The one player-visible change in this branch, pinned.

    ``Black-Augurite.png`` and ``Peat-Block.png`` are the only sprite filenames
    that are BOTH capitalized and a real items.csv row. Before the fold they
    raw-missed, so ``get_item_price`` returned None; ``daily_item_list`` filters
    on ``price == 0``, ``None == 0`` is False, and both shipped into the daily
    Mart with a null price. Folded, they resolve to their real cost of 0 and the
    existing filter drops them — the Mart goes from 188 entries to 186.

    That is the correct outcome (a null-priced Mart entry was never buyable in
    any meaningful sense, and both are still granted by ``random_item()``), but
    it IS a behaviour change, so it gets a test rather than a surprise.
    """
    import Ankimon.utils as utils

    assert utils.get_item_price("Black-Augurite") == 0
    assert utils.get_item_price("Peat-Block") == 0
    assert utils.get_item_id("Black-Augurite") == 10001
    assert utils.get_item_id("Peat-Block") == 10002
    # The lowercase identifiers always resolved; the capitalization was the only
    # thing standing between the sprite name and the row.
    assert utils.get_item_price("black-augurite") == 0
    assert utils.get_item_price("peat-block") == 0
