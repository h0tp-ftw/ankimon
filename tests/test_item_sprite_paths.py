"""Bag icons resolve display names and the downloaded pack's exact filenames."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from test_web_bag_trade_evolutions import shop_obj  # noqa: F401


def _sprite_pack(monkeypatch, tmp_path, filenames=()):
    import Ankimon.utils as utils

    items = tmp_path / "items"
    available = {str(items / filename) for filename in filenames}
    monkeypatch.setattr(utils, "items_path", items)
    # macOS normally ignores filename case. Emulate the pack on Linux so a
    # lowercase lookup cannot accidentally pass for Black-Augurite.png.
    monkeypatch.setattr(Path, "is_file", lambda path: str(path) in available)
    return utils, items


@pytest.mark.parametrize(
    ("identifier", "filename"),
    [
        ("black-augurite", "Black-Augurite.png"),
        ("peat-block", "Peat-Block.png"),
    ],
)
def test_canonical_names_find_capitalized_pack_sprites(
    monkeypatch, tmp_path, identifier, filename
):
    utils, items = _sprite_pack(monkeypatch, tmp_path, [filename])

    assert utils.get_item_sprite_path(identifier) == items / filename


@pytest.mark.parametrize(
    ("display_name", "filename"),
    [
        ("King's Rock", "kings-rock.png"),
        ("Poké Ball", "poke-ball.png"),
        ("  Dubious Disc  ", "dubious-disc.png"),
        ("Black Augurite", "Black-Augurite.png"),
        ("Peat Block", "Peat-Block.png"),
    ],
)
def test_display_names_find_canonical_or_legacy_sprites(
    monkeypatch, tmp_path, display_name, filename
):
    utils, items = _sprite_pack(monkeypatch, tmp_path, [filename])

    assert utils.get_item_sprite_path(display_name) == items / filename


@pytest.mark.parametrize("name", ["Linking Cord", "Health Mochi", "Quick Candy XL"])
def test_display_names_reach_bundled_fallback(monkeypatch, tmp_path, name):
    utils, _ = _sprite_pack(monkeypatch, tmp_path)
    identifier = utils.normalize_item_identifier(name)

    assert utils.get_item_sprite_path(name) == utils._BUNDLED_ITEM_SPRITES[identifier]


def test_downloaded_sprite_takes_priority_over_bundled_fallback(monkeypatch, tmp_path):
    utils, items = _sprite_pack(monkeypatch, tmp_path, ["linking-cord.png"])

    assert utils.get_item_sprite_path("Linking Cord") == items / "linking-cord.png"


def test_exact_downloaded_filename_takes_priority_over_normalization(monkeypatch, tmp_path):
    utils, items = _sprite_pack(
        monkeypatch, tmp_path, ["Linking Cord.png", "linking-cord.png"]
    )

    assert utils.get_item_sprite_path("Linking Cord") == items / "Linking Cord.png"


def test_canonical_download_takes_priority_over_legacy_pack_filename(monkeypatch, tmp_path):
    utils, items = _sprite_pack(
        monkeypatch, tmp_path, ["black-augurite.png", "Black-Augurite.png"]
    )

    assert utils.get_item_sprite_path("black-augurite") == items / "black-augurite.png"


def test_unknown_item_retains_original_expected_path(monkeypatch, tmp_path):
    utils, items = _sprite_pack(monkeypatch, tmp_path)

    assert utils.get_item_sprite_path("Unknown Item") == items / "Unknown Item.png"


@pytest.mark.parametrize(
    ("move_name", "move_type"),
    [("flamethrower", "Fire"), ("surf", "Water"), ("thunderbolt", "Electric")],
)
def test_web_bag_tm_urls_match_downloaded_type_filename(
    shop_obj, monkeypatch, tmp_path, move_name, move_type
):
    from PyQt6.QtCore import QUrl
    import Ankimon.localized_text  # import before Path.is_file is restricted

    filename = f"Bag_TM_{move_type.lower()}_SV_Sprite.png"
    utils, items = _sprite_pack(monkeypatch, tmp_path, [filename])
    monkeypatch.setattr(shop_obj, "QUrl", QUrl)
    monkeypatch.setattr(shop_obj, "get_item_sprite_path", utils.get_item_sprite_path)
    host = SimpleNamespace(
        _categorize=lambda name, is_tm: "tm",
        _coerce_int=shop_obj.AnkimonItemsWeb._coerce_int,
    )

    entry = shop_obj.AnkimonItemsWeb._serialize_item(
        host,
        name=move_name,
        is_tm=True,
        in_shop=False,
        shop_price=None,
        item_type="TM",
        owned_quantity=1,
    )

    icon = Path(QUrl(entry["image_url"]).toLocalFile())
    assert entry["move_type"] == move_type
    assert icon == items / filename
    assert icon.is_file()
