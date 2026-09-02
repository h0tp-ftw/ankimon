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
