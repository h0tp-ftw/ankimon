import pytest


@pytest.fixture(autouse=True)
def reset_random_item_cache():
    """Each sprite-directory scenario starts with its own item cache."""
    import Ankimon.utils as utils

    utils.clear_utils_caches()
    yield
    utils.clear_utils_caches()


def test_random_item_returns_none_when_sprite_directory_is_missing(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    missing = tmp_path / "missing-items"
    granted = []
    monkeypatch.setattr(utils, "items_path", missing)
    monkeypatch.setattr(utils, "give_item", granted.append)


    # We mock _BUNDLED_ITEM_SPRITES to empty so that it tests purely on items_path
    monkeypatch.setattr(utils, "_BUNDLED_ITEM_SPRITES", {})
    assert utils.random_item() is None

    assert granted == []


def test_random_item_returns_none_when_no_eligible_sprites_exist(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    (tmp_path / "master-ball.png").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("not a sprite", encoding="utf-8")
    granted = []
    monkeypatch.setattr(utils, "items_path", tmp_path)
    monkeypatch.setattr(utils, "give_item", granted.append)


    # We mock _BUNDLED_ITEM_SPRITES to empty so that it tests purely on items_path
    monkeypatch.setattr(utils, "_BUNDLED_ITEM_SPRITES", {})
    assert utils.random_item() is None

    assert granted == []


def test_random_item_grants_an_eligible_item(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    (tmp_path / "potion.png").write_bytes(b"")
    granted = []
    monkeypatch.setattr(utils, "items_path", tmp_path)
    monkeypatch.setattr(utils, "give_item", granted.append)


    monkeypatch.setattr(utils, "_BUNDLED_ITEM_SPRITES", {})
    assert utils.random_item() == "potion"

    assert granted == ["potion"]


def test_random_item_uses_registered_bundled_sprites(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    bundled_sprite = tmp_path / "bundled" / "linking-cord.png"
    bundled_sprite.parent.mkdir()
    bundled_sprite.touch()
    (bundled_sprite.parent / "unregistered-item.png").touch()
    granted = []
    monkeypatch.setattr(utils, "items_path", tmp_path / "missing-downloads")
    monkeypatch.setattr(
        utils, "_BUNDLED_ITEM_SPRITES", {"linking-cord": bundled_sprite}
    )
    monkeypatch.setattr(utils, "give_item", granted.append)

    assert utils.random_item() == "linking-cord"
    assert granted == ["linking-cord"]
    assert utils.get_item_sprite_path("linking-cord") == bundled_sprite


def test_random_item_pool_is_cached_until_cleared(monkeypatch, tmp_path):
    import Ankimon.utils as utils

    (tmp_path / "super-potion.png").touch()
    monkeypatch.setattr(utils, "items_path", tmp_path)
    monkeypatch.setattr(utils, "_BUNDLED_ITEM_SPRITES", {})
    monkeypatch.setattr(utils, "give_item", lambda _name: None)
    monkeypatch.setattr(utils.random, "choice", lambda names: sorted(names)[0])

    assert utils.random_item() == "super-potion"
    (tmp_path / "hyper-potion.png").touch()
    assert utils._random_item_cache == ["super-potion"]

    utils.clear_utils_caches()
    assert utils.random_item() == "hyper-potion"
    assert set(utils._random_item_cache) == {"super-potion", "hyper-potion"}
