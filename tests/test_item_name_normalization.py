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
