def test_random_item_returns_none_when_sprite_directory_is_missing(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    missing = tmp_path / "missing-items"
    granted = []
    monkeypatch.setattr(utils, "items_path", missing)
    monkeypatch.setattr(utils, "give_item", granted.append)

    assert utils.random_item() is None
    assert granted == []


def test_random_item_returns_none_when_no_eligible_sprites_exist(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    (tmp_path / "master-ball.png").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("not a sprite", encoding="utf-8")
    granted = []
    monkeypatch.setattr(utils, "items_path", tmp_path)
    monkeypatch.setattr(utils, "give_item", granted.append)

    assert utils.random_item() is None
    assert granted == []


def test_random_item_grants_an_eligible_item(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    (tmp_path / "potion.png").write_bytes(b"")
    granted = []
    monkeypatch.setattr(utils, "items_path", tmp_path)
    monkeypatch.setattr(utils, "give_item", granted.append)

    assert utils.random_item() == "potion"
    assert granted == ["potion"]
