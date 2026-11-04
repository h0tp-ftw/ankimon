from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "src" / "Ankimon"
UPDATE_PAGE = ADDON / "ankimon_manager_web" / "update_manager"

WEB_PAGES = (
    "ankimon_items_web/shop.html",
    "ankimon_items_web/settings.html",
    "ankidex/ankidex.html",
    "ankimon_profile_web/profile.html",
    "ankimon_profile_web/team.html",
    "ankimon_mobile_web/mobile.html",
    "ankimon_mobile_web/history.html",
    "ankimon_manager_web/backup_manager/backup_manager.html",
)


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.update_manager_links = 0
        self.local_resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-screen") == "update_manager":
            self.update_manager_links += 1
        resource = (
            attrs.get("src")
            if tag == "script"
            else attrs.get("href")
            if tag == "link"
            else None
        )
        if resource and not urlparse(resource).scheme and not resource.startswith("qrc:"):
            self.local_resources.append(resource.split("?", 1)[0])


def test_existing_web_ui_pages_route_to_update_manager_and_keep_assets_resolvable():
    pages = (
        *WEB_PAGES,
        "ankimon_manager_web/update_manager/update_manager.html",
    )
    for relative_path in pages:
        page = ADDON / relative_path
        parser = PageLinks()
        parser.feed(page.read_text(encoding="utf-8"))

        assert parser.update_manager_links == 1, relative_path
        assert all((page.parent / item).exists() for item in parser.local_resources)


def test_update_manager_markup_exposes_required_panels_and_statuses():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    assert 'src="../../user_files/sprites/items/exp-share.png"' in html
    assert 'id="pr-search"' in html
    assert "Status: Code error [Traceback]" in html
    assert "Status: Could not check connection." in javascript
    assert "window.onUpdateCodeError" in javascript
    assert "setCodeError(data.message, data.traceback)" in javascript
    assert "window.onUpdateLoadFailed" in javascript
    assert "Could not check for updates" in javascript
    assert "String(entry.number)" in javascript
    assert "entry.title" in javascript
    assert "update-card" in css


def test_shell_routes_update_manager_without_replacing_help_dialog():
    shell = (ADDON / "ankimon_items_web" / "shop_obj.py").read_text(
        encoding="utf-8"
    )
    menu = (ADDON / "menu_buttons.py").read_text(encoding="utf-8")

    assert 'SCREEN_UPDATE = "update_manager"' in shell
    assert "def openUpdateManager(self)" in shell
    assert "update_manager" in shell
    assert "from .pyobj.update_dialog import UpdateDialog" in menu
