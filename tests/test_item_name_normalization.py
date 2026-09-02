def test_return_id_for_item_name_normalization():
    # Because of Anki imports in main `__init__`, we need to import pokedex_functions specifically
    from Ankimon.functions.pokedex_functions import return_id_for_item_name

    # Should resolve correctly despite casing/spacing/punctuation
    assert return_id_for_item_name("Dubious Disc") == "301"
    assert return_id_for_item_name("King's Rock") == "198"
    assert return_id_for_item_name("Up-Grade") == "229"
