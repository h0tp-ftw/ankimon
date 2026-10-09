"""Qt characterization tests for the Pokémon PC "Current Party" column.

The party column (``PokemonPC._build_party_panel`` / ``refresh_party_panel`` /
``_populate_party_slot``) shows the six team slots left of the box grid, hides
party members from the grid query and offers "Add to team" / "Remove from team"
in the slot context menu. These pin that contract with ``pc_box.py`` loaded in
isolation (every module-level dependency stubbed, real PyQt6 widgets), the same
bootstrap as ``test_pc_box_evolution_button.py``. Run standalone::

    pytest tests/test_pc_box_party_panel.py
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.


_MODULE_NAME = "Ankimon.pyobj.pc_box"
_SRC = Path(__file__).parent.parent / "src"


@pytest.fixture(autouse=True)
def _env_guard():
    """Skip when another test file has mocked PyQt6 in this process."""
    from PyQt6.QtWidgets import QDialog

    if not isinstance(QDialog, type):  # PyQt6 was mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_pc_box_party_panel.py standalone"
        )
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    yield


def _make_module(name, **attrs):
    """Build a throwaway module exposing ``attrs``."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def pc_box(qapp):
    """Force-load ``pc_box.py`` with its heavyweight imports stubbed.

    ``Ankimon.functions.team_functions`` is loaded for real (it is aqt-free) so
    the tests exercise the genuine seam between the window and the team table.
    """
    from PyQt6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QVBoxLayout,
        QLabel,
        QPushButton,
        QGridLayout,
    )
    from PyQt6.QtGui import QPixmap, QFont
    from PyQt6.QtCore import Qt

    fake_hooks = mock.MagicMock()
    aqt_mod = _make_module("aqt", mw=mock.MagicMock(), gui_hooks=fake_hooks)
    aqt_qt_mod = _make_module(
        "aqt.qt",
        Qt=Qt,
        QDialog=QDialog,
        QHBoxLayout=QHBoxLayout,
        QVBoxLayout=QVBoxLayout,
        QLabel=QLabel,
        QPushButton=QPushButton,
        QGridLayout=QGridLayout,
        QPixmap=QPixmap,
    )
    aqt_theme_mod = _make_module(
        "aqt.theme", theme_manager=types.SimpleNamespace(night_mode=False)
    )

    services_obj = types.SimpleNamespace(
        db=None, achievements={}, logger=None, trainer_card=None
    )
    services_mod = _make_module("Ankimon.services", services=services_obj)

    class _Stub:
        """Constructible placeholder for classes pc_box only references."""

        def __init__(self, *a, **k):
            """Accept anything."""

        @classmethod
        def from_dict(cls, *a, **k):
            """Return a bare instance."""
            return cls()

        @staticmethod
        def calc_stat(*a, **k):
            """Constant stat."""
            return 1

    stub_specs = {
        "Ankimon.pyobj.pokemon_obj": {"PokemonObject": _Stub},
        "Ankimon.pyobj.reviewer_obj": {"Reviewer_Manager": _Stub},
        "Ankimon.pyobj.test_window": {"TestWindow": _Stub},
        "Ankimon.pyobj.translator": {"Translator": _Stub},
        "Ankimon.pyobj.collection_dialog": {"MainPokemon": _Stub},
        "Ankimon.gui_classes.pokemon_details": {
            "PokemonCollectionDetailsSplit": lambda *a, **k: (None, None, None, {}),
            "remember_attack": mock.MagicMock(),
        },
        "Ankimon.pyobj.InfoLogger": {"ShowInfoLogger": _Stub},
        "Ankimon.pyobj.move_picker": {"MovePickerDialog": _Stub},
        "Ankimon.pyobj.evolution_window": {"EvoWindow": _Stub},
        "Ankimon.pyobj.settings": {"Settings": _Stub},
        "Ankimon.functions.friendship_evolution": {
            "current_time_label": lambda *a, **k: "Day",
            "evolution_readiness": lambda *a, **k: {"ready": False, "method": None},
        },
        "Ankimon.functions.sprite_functions": {"get_sprite_path": lambda *a, **k: ""},
        "Ankimon.utils": {
            "load_custom_font": lambda *a, **k: QFont(),
            "get_tier_by_id": lambda *a, **k: "Normal",
            "is_alive": lambda obj: obj is not None,
            "format_move_name": lambda s: str(s).replace("-", " ").title(),
            "format_pokemon_name": lambda s: str(s).title(),
        },
        "Ankimon.resources": {
            "icon_path": Path("/nonexistent/icon.png"),
            "items_path": Path("/nonexistent/items"),
            "csv_file_items_cost": Path("/nonexistent/items_cost.csv"),
            "poke_evo_path": Path("/nonexistent/evo"),
            "pokemon_tm_learnset_path": Path("/nonexistent/tm.json"),
            "addon_dir": Path("/nonexistent/addon"),
        },
        "Ankimon.business": {"calculate_cp_from_dict": lambda *a, **k: 0},
        "Ankimon.functions.pokedex_functions": {
            "find_details_move": lambda m: {"type": "Normal"},
            "get_all_pokemon_moves": lambda *a, **k: [],
            "format_lore_name": lambda s: str(s).title(),
            "get_pretty_name_for_name": lambda s: str(s).title(),
            "search_pokedex_by_id": lambda i: "pikachu",
        },
        "Ankimon.functions.gui_functions": {
            "type_icon_path": lambda *a, **k: Path("/nonexistent"),
            "move_category_path": lambda *a, **k: Path("/nonexistent"),
        },
        "Ankimon.functions.tm_learnset": {"get_tm_learnset": lambda *a, **k: {}},
    }

    parent_pkgs = (
        "Ankimon",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "Ankimon.gui_classes",
    )
    to_install = {
        "aqt": aqt_mod,
        "aqt.qt": aqt_qt_mod,
        "aqt.theme": aqt_theme_mod,
        "Ankimon.services": services_mod,
        _MODULE_NAME: None,
        "Ankimon.functions.team_functions": None,
        **{name: _make_module(name, **attrs) for name, attrs in stub_specs.items()},
    }
    saved = {name: sys.modules.get(name) for name in (*to_install, *parent_pkgs)}

    for pkg in parent_pkgs:
        mod = types.ModuleType(pkg)
        mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
        mod.__package__ = pkg
        sys.modules[pkg] = mod
    for name, mod in to_install.items():
        if mod is not None:
            sys.modules[name] = mod
    sys.modules.pop("Ankimon.functions.team_functions", None)

    try:
        spec = importlib.util.spec_from_file_location(
            _MODULE_NAME, _SRC / "Ankimon" / "pyobj" / "pc_box.py"
        )
        module = importlib.util.module_from_spec(spec)
        module._services_obj = services_obj  # expose for tests
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, val in saved.items():
            if val is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = val


class _Translator:
    """Echoes the key plus kwargs so assertions can see which key was chosen."""

    def translate(self, key, **kwargs):
        """Return ``key`` followed by any formatting arguments."""
        if key == "level_label":
            return f"Lvl {kwargs.get('level')}"
        return key if not kwargs else f"{key}:{kwargs}"


def _host(pc_box, members=None):
    """A PokemonPC-shaped namespace with the real party methods bound to it."""
    settings = mock.MagicMock()
    settings.get.return_value = 1  # misc.language -> int()
    host = types.SimpleNamespace(
        theme_vars={
            "button_border": "#6A73D9",
            "background_color": "#003A70",
            "slot_bg_color": "#002B5A",
            "favorite_color": "#998A3D",
            "favorite_hover_color": "#8A7C36",
            "hover_color": "#6A73D9",
        },
        translator=_Translator(),
        settings=settings,
        logger=mock.MagicMock(),
        show_sprites_across_ankimon=True,
        gif_in_collection=False,
        party_slot_width=180,
        party_slot_height=62,
        party_slots=[],
        party_panel=None,
        _party_members=list(members or []),
        _party_ids=[],
        show_pokemon_details=mock.MagicMock(),
        show_actions_submenu=mock.MagicMock(),
        on_party_slot_drop=mock.MagicMock(),
        on_box_drop=mock.MagicMock(),
    )
    for name in ("_build_party_panel", "refresh_party_panel", "_populate_party_slot"):
        setattr(
            host, name, types.MethodType(getattr(pc_box.PokemonPC, name), host)
        )
    return host


def _member(ind_id, name="lickilicky", level=25, gender="F", **extra):
    """A full saved-Pokémon record as ``load_party`` returns it."""
    record = {
        "individual_id": ind_id,
        "name": name,
        "id": 463,
        "level": level,
        "gender": gender,
        "nickname": "",
        "shiny": False,
        "is_favorite": False,
    }
    record.update(extra)
    return record


def test_party_exclusion_sql_hides_members_and_is_empty_without_team(pc_box):
    """The grid query excludes every party id; no party means no clause."""
    assert pc_box.party_exclusion_sql([]) == ("", [])
    clause, params = pc_box.party_exclusion_sql(["a", "", "b", None])
    assert clause == "AND individual_id NOT IN (?,?)"
    assert params == ["a", "b"]


def test_build_party_panel_creates_six_empty_slots(pc_box):
    """The column holds exactly MAX_TEAM_SIZE disabled, dashed, empty slots."""
    host = _host(pc_box)
    panel = host._build_party_panel()
    assert panel is host.party_panel
    assert len(host.party_slots) == pc_box.MAX_TEAM_SIZE == 6
    for index, slot in enumerate(host.party_slots):
        assert isinstance(slot, pc_box.PartySlotButton)
        assert slot._slot_index == index
        assert slot._individual_id is None and slot._member is None
        assert slot.isEnabled()  # stays enabled so it can take drops
        assert slot.acceptDrops()
        assert slot._name_label.text() == "pc_party_empty_slot"
        assert "dashed" in slot.styleSheet()


def test_populate_party_slot_shows_name_level_and_gender(pc_box):
    """A filled slot is clickable and carries the record for details/menus."""
    host = _host(pc_box)
    host._build_party_panel()
    slot = host.party_slots[0]
    member = _member("lick-1", shiny=True)

    host._populate_party_slot(slot, member)

    assert slot.isEnabled()
    assert slot._individual_id == "lick-1"
    assert slot._member is member
    assert slot._name_label.text() == "Lickilicky"
    assert slot._sub_label.text() == "Lvl 25 ♀ ⭐"
    assert slot._base_bg == host.theme_vars["slot_bg_color"]
    assert "Lickilicky" in slot.toolTip()

    # Emptying the same slot again clears the record.
    host._populate_party_slot(slot, None)
    assert slot._individual_id is None and slot._member is None


def test_populate_party_slot_prefers_nickname_and_favorite_colour(pc_box):
    """Nickname wins over species; favourites use the favourite palette."""
    host = _host(pc_box)
    host._build_party_panel()
    slot = host.party_slots[2]
    host._populate_party_slot(
        slot, _member("x", nickname="Rose", gender="M", is_favorite=True)
    )
    assert slot._name_label.text() == "Rose"
    assert slot._sub_label.text() == "Lvl 25 ♂"
    assert slot._base_bg == host.theme_vars["favorite_color"]


def test_refresh_party_panel_fills_from_load_party_in_order(pc_box, monkeypatch):
    """Slots mirror ``load_party`` order; the rest stay empty; ids are cached."""
    host = _host(pc_box)
    host._build_party_panel()
    members = [_member("a", name="jade"), _member("b", name="diamond")]
    monkeypatch.setattr(pc_box, "load_party", lambda db, logger=None: list(members))

    host.refresh_party_panel()

    assert host._party_ids == ["a", "b"]
    assert [s._individual_id for s in host.party_slots] == ["a", "b", None, None, None, None]
    assert host.party_slots[1]._name_label.text() == "Diamond"
    assert host.party_slots[2]._member is None


def test_party_slot_click_routes_to_details_and_menu(pc_box, monkeypatch):
    """Left click shows details for the member; right click opens the party menu."""
    host = _host(pc_box)
    host._build_party_panel()
    member = _member("m1")
    monkeypatch.setattr(pc_box, "load_party", lambda db, logger=None: [member])
    host.refresh_party_panel()
    slot = host.party_slots[0]

    slot.clicked.emit(False)
    host.show_pokemon_details.assert_called_once_with(member)

    slot.rightClicked.emit()
    host.show_actions_submenu.assert_called_once_with(slot, member, in_party=True)

    # An empty slot never forwards anything.
    empty = host.party_slots[1]
    empty.clicked.emit(False)
    empty.rightClicked.emit()
    assert host.show_pokemon_details.call_count == 1
    assert host.show_actions_submenu.call_count == 1


def test_update_count_label_excludes_party_from_box_total(pc_box):
    """"Showing X / Y" counts boxed Pokémon only."""
    from PyQt6.QtWidgets import QLabel

    host = types.SimpleNamespace(
        count_label=QLabel(),
        _filtered_pokemon=[1, 2, 3],
        _total_pokemon_count=52,
        _party_members=[{}] * 6,
    )
    pc_box.PokemonPC._update_count_label(host)
    assert host.count_label.text() == "Showing 3 / 46 Pokémon"


def test_build_team_action_add_remove_and_full_party(pc_box):
    """Boxed Pokémon get "Add to team" (disabled when full); members get "Remove"."""
    from PyQt6.QtWidgets import QMenu, QWidget

    holder = QWidget()
    holder.translator = _Translator()
    holder.add_to_team = mock.MagicMock()
    holder.remove_from_team = mock.MagicMock()
    pokemon = {"individual_id": "p", "name": "eevee"}

    holder._party_members = []
    menu = QMenu(holder)
    add = pc_box.PokemonPC._build_team_action(holder, menu, pokemon, in_party=False)
    assert add.text() == "pc_add_to_team" and add.isEnabled()
    add.trigger()
    holder.add_to_team.assert_called_once_with(pokemon)

    holder._party_members = [{}] * pc_box.MAX_TEAM_SIZE
    full = pc_box.PokemonPC._build_team_action(holder, menu, pokemon, in_party=False)
    assert not full.isEnabled()
    assert full.toolTip().startswith("pc_team_full")
    assert menu.toolTipsVisible()

    remove = pc_box.PokemonPC._build_team_action(holder, menu, pokemon, in_party=True)
    assert remove.text() == "pc_remove_from_team" and remove.isEnabled()
    remove.trigger()
    holder.remove_from_team.assert_called_once_with(pokemon)


def _report_host(pc_box):
    """A mock PokemonPC for ``_report_team_change`` with the real outcome map."""
    host = mock.MagicMock()
    host.translator = _Translator()
    host._TEAM_OUTCOME_KEYS = pc_box.PokemonPC._TEAM_OUTCOME_KEYS
    return host


def test_report_team_change_success_refreshes_and_toasts(pc_box):
    """A successful add refreshes the grid and shows a non-blocking message."""
    TeamChange = sys.modules["Ankimon.functions.team_functions"].TeamChange
    host = _report_host(pc_box)
    stub = {"individual_id": "p", "name": "eevee", "nickname": "Vee"}

    pc_box.PokemonPC._report_team_change(host, TeamChange(True, "added", slot=3), stub)

    host.refresh_pokemon_grid.assert_called_once_with()
    host._toast.assert_called_once()
    message = host._toast.call_args.args[0]
    assert message.startswith("pc_added_to_team") and "'Vee'" in message and "'slot': 3" in message
    host.logger.log_and_showinfo.assert_not_called()


def test_report_team_change_full_party_warns_without_refresh(pc_box):
    """A refused add tells the user and leaves the (still correct) grid alone."""
    TeamChange = sys.modules["Ankimon.functions.team_functions"].TeamChange
    host = _report_host(pc_box)
    stub = {"individual_id": "p", "name": "eevee"}

    pc_box.PokemonPC._report_team_change(host, TeamChange(False, "team_full"), stub)

    host.refresh_pokemon_grid.assert_not_called()
    host._toast.assert_not_called()
    level, message = host.logger.log_and_showinfo.call_args.args
    assert level == "info" and message.startswith("pc_team_full")

    # Stale membership outcomes re-read the DB so the window catches up.
    pc_box.PokemonPC._report_team_change(host, TeamChange(False, "already_on_team"), stub)
    host.refresh_pokemon_grid.assert_called_once_with()


def test_add_and_remove_go_through_the_team_seam(pc_box, monkeypatch):
    """The window delegates to ``add_to_party`` / ``remove_from_party`` with services.db."""
    team_mod = sys.modules["Ankimon.functions.team_functions"]
    pc_box._services_obj.db = fake_db = object()
    added = team_mod.TeamChange(True, "added", slot=1)
    removed = team_mod.TeamChange(True, "removed")
    add_spy = mock.MagicMock(return_value=added)
    remove_spy = mock.MagicMock(return_value=removed)
    monkeypatch.setattr(pc_box, "add_to_party", add_spy)
    monkeypatch.setattr(pc_box, "remove_from_party", remove_spy)
    host = mock.MagicMock()
    stub = {"individual_id": "p", "name": "eevee"}

    pc_box.PokemonPC.add_to_team(host, stub)
    add_spy.assert_called_once_with(fake_db, "p", host.logger)
    host._report_team_change.assert_called_once_with(added, stub)

    pc_box.PokemonPC.remove_from_team(host, stub)
    remove_spy.assert_called_once_with(fake_db, "p", host.logger)
    host._report_team_change.assert_called_with(removed, stub)


def test_slot_selection_covers_party_slots(pc_box, monkeypatch):
    """The white selection ring is applied to a selected party member too."""
    from PyQt6.QtWidgets import QGridLayout

    host = _host(pc_box)
    host._build_party_panel()
    monkeypatch.setattr(pc_box, "load_party", lambda db, logger=None: [_member("sel")])
    host.refresh_party_panel()
    host.pokemon_grid = QGridLayout()
    host._selected_individual_id = "sel"
    host._slot_widgets = types.MethodType(pc_box.PokemonPC._slot_widgets, host)

    pc_box.PokemonPC._refresh_slot_selection(host)

    assert "3px solid #ffffff" in host.party_slots[0].styleSheet()
    assert "dashed" in host.party_slots[1].styleSheet()  # empty slot untouched


class _Drop:
    """A drag-enter or drop event plus the mime object it borrows.

    ``QDropEvent`` only stores a pointer to the ``QMimeData``; the Python side
    must keep that object alive while the event is dispatched, so the two are
    bundled here and the event is dispatched through :meth:`send`.
    """

    def __init__(self, pc_box, individual_id, source, kind="drop"):
        """Build the event for a Pokémon drag that started at ``source``."""
        from PyQt6.QtCore import Qt, QPoint, QPointF
        from PyQt6.QtGui import QDragEnterEvent, QDropEvent

        self.mime = pc_box.encode_drag_payload(individual_id, source)
        if kind == "enter":
            self.event = QDragEnterEvent(
                QPoint(5, 5),
                Qt.DropAction.MoveAction,
                self.mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        else:
            self.event = QDropEvent(
                QPointF(5, 5),
                Qt.DropAction.MoveAction,
                self.mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )

    def send(self, widget):
        """Dispatch to the matching handler and return whether it was accepted."""
        from PyQt6.QtGui import QDragEnterEvent

        if isinstance(self.event, QDragEnterEvent):
            widget.dragEnterEvent(self.event)
        else:
            widget.dropEvent(self.event)
        return self.event.isAccepted()


def test_drag_payload_round_trips_and_rejects_garbage(pc_box):
    """The mime payload survives encode/decode; foreign or malformed data is None."""
    from PyQt6.QtCore import QMimeData

    mime = pc_box.encode_drag_payload("abc", "party")
    assert mime.hasFormat(pc_box.POKEMON_MIME)
    assert pc_box.decode_drag_payload(mime) == ("abc", "party")

    assert pc_box.decode_drag_payload(None) is None
    text = QMimeData()
    text.setText("abc")
    assert pc_box.decode_drag_payload(text) is None
    bad = QMimeData()
    bad.setData(pc_box.POKEMON_MIME, b"{not json")
    assert pc_box.decode_drag_payload(bad) is None
    wrong_source = QMimeData()
    wrong_source.setData(pc_box.POKEMON_MIME, b'{"individual_id": "x", "source": "moon"}')
    assert pc_box.decode_drag_payload(wrong_source) is None


def test_party_slot_accepts_box_and_party_drags_and_reports_drop(pc_box, monkeypatch):
    """A party slot highlights on enter, emits (id, source) on drop, ignores itself."""
    host = _host(pc_box)
    host._build_party_panel()
    monkeypatch.setattr(pc_box, "load_party", lambda db, logger=None: [_member("m1")])
    host.refresh_party_panel()
    slot = host.party_slots[0]
    received = []
    slot.pokemonDropped.connect(lambda ind_id, source: received.append((ind_id, source)))

    assert _Drop(pc_box, "boxed", "box", "enter").send(slot)
    assert pc_box.DROP_HIGHLIGHT_BORDER in slot.styleSheet()

    assert _Drop(pc_box, "boxed", "box").send(slot)
    assert received == [("boxed", "box")]
    host.on_party_slot_drop.assert_called_with(0, "boxed", "box")
    assert pc_box.DROP_HIGHLIGHT_BORDER not in slot.styleSheet()  # highlight cleared

    assert _Drop(pc_box, "other", "party").send(slot)
    assert received[-1] == ("other", "party")

    # Dropping a slot onto itself is ignored.
    assert not _Drop(pc_box, "m1", "party").send(slot)
    assert len(received) == 2

    # Empty slots are drop targets too.
    assert _Drop(pc_box, "boxed", "box", "enter").send(host.party_slots[3])


def test_box_slot_and_grid_take_party_members_only(pc_box):
    """Box slots and the grid background accept party drags and refuse box drags."""
    box_slot = pc_box.PokemonSlotButton("")
    box_slot._individual_id = "boxed"
    got = []
    box_slot.pokemonDropped.connect(lambda i, s: got.append((i, s)))

    assert not _Drop(pc_box, "another", "box").send(box_slot)
    assert got == []

    assert _Drop(pc_box, "member", "party").send(box_slot)
    assert got == [("member", "party")]

    area = pc_box.BoxDropArea()
    area_got = []
    area.pokemonDropped.connect(lambda i, s: area_got.append((i, s)))
    assert not _Drop(pc_box, "x", "box").send(area)
    assert _Drop(pc_box, "member", "party").send(area)
    assert area_got == [("member", "party")]


def test_drop_handlers_route_to_place_move_and_remove(pc_box, monkeypatch):
    """Party-slot drops place (box) or move (party); grid drops remove party members."""
    team_mod = sys.modules["Ankimon.functions.team_functions"]
    fake_db = mock.MagicMock()
    fake_db.get_pokemon.return_value = {"individual_id": "p", "name": "eevee"}
    pc_box._services_obj.db = fake_db
    place = mock.MagicMock(return_value=team_mod.TeamChange(True, "added", slot=2))
    move = mock.MagicMock(return_value=team_mod.TeamChange(True, "moved", slot=1))
    remove = mock.MagicMock(return_value=team_mod.TeamChange(True, "removed"))
    monkeypatch.setattr(pc_box, "place_in_party", place)
    monkeypatch.setattr(pc_box, "move_in_party", move)
    monkeypatch.setattr(pc_box, "remove_from_party", remove)
    host = mock.MagicMock()
    host._record_for_message = types.MethodType(pc_box.PokemonPC._record_for_message, host)

    pc_box.PokemonPC.on_party_slot_drop(host, 1, "p", "box")
    place.assert_called_once_with(fake_db, "p", 1, host.logger)
    pc_box.PokemonPC.on_party_slot_drop(host, 0, "p", "party")
    move.assert_called_once_with(fake_db, "p", 0, host.logger)
    pc_box.PokemonPC.on_box_drop(host, "p", "box")
    remove.assert_not_called()
    pc_box.PokemonPC.on_box_drop(host, "p", "party")
    remove.assert_called_once_with(fake_db, "p", host.logger)
    assert host._report_team_change.call_count == 3
    # Messages are built from the stored record, with a stand-in when it is gone.
    fake_db.get_pokemon.return_value = None
    assert host._record_for_message("gone") == {"individual_id": "gone", "name": "???"}


def test_report_team_change_swapped_names_the_replaced_pokemon(pc_box):
    """A swap message carries both the newcomer and the Pokémon sent back."""
    TeamChange = sys.modules["Ankimon.functions.team_functions"].TeamChange
    host = _report_host(pc_box)
    host._record_for_message = lambda ind_id: {"individual_id": ind_id, "name": "snorlax"}
    stub = {"individual_id": "p", "name": "eevee"}

    pc_box.PokemonPC._report_team_change(
        host, TeamChange(True, "swapped", slot=1, replaced="old"), stub
    )

    message = host._toast.call_args.args[0]
    assert message.startswith("pc_swapped_into_team")
    assert "'replaced_name': 'Snorlax'" in message and "'slot': 1" in message
    host.refresh_pokemon_grid.assert_called_once_with()


def test_drag_starts_only_after_the_threshold_and_only_with_a_pokemon(pc_box, monkeypatch):
    """A left press plus a long enough move starts a drag; small moves and empty slots do not."""
    from PyQt6.QtCore import Qt, QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    slot = pc_box.PokemonSlotButton("")
    slot.resize(75, 75)
    started = []
    monkeypatch.setattr(slot, "start_drag", lambda: started.append(True))
    far = QApplication.startDragDistance() + 5

    def press():
        """Left press at the slot's origin."""
        slot.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(0, 0),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def move(dx):
        """Mouse move with the left button held, ``dx`` pixels to the right."""
        slot.mouseMoveEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseMove,
                QPointF(dx, 0),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    # No Pokémon in the slot: never drags.
    press()
    move(far)
    assert started == []

    # A Pokémon but a tiny move: not yet.
    slot._individual_id = "p"
    press()
    move(1)
    assert started == []

    # Past the platform threshold: one drag, and the press is consumed.
    move(far)
    assert started == [True]
    move(far * 2)
    assert started == [True]
