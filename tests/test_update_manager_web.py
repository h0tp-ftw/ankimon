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
        self.nav_screens = []
        self.local_resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-screen") == "update_manager":
            self.update_manager_links += 1
        if attrs.get("role") == "menuitem" and attrs.get("data-screen"):
            self.nav_screens.append(attrs["data-screen"])
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
        assert parser.nav_screens.index("update_manager") < parser.nav_screens.index(
            "backup_manager"
        ), relative_path
        assert all((page.parent / item).exists() for item in parser.local_resources)


def test_update_manager_markup_exposes_required_panels_and_statuses():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    assert 'src="../../user_files/sprites/items/exp-share.png"' in html
    assert 'id="pr-search"' in html
    assert 'id="pr-pagination"' in html
    assert 'id="pr-page-summary"' in html
    assert 'id="pr-page-limit"' in html
    assert "Max 100 PRs shown" in html
    assert "Showing PR #" in javascript
    assert "const PR_PAGE_SIZE = 50;" in javascript
    assert (
        "entries.slice(prPage * PR_PAGE_SIZE, (prPage + 1) * PR_PAGE_SIZE)"
        in javascript
    )
    assert "status-text.neutral" in css
    assert "No updates since last install" in javascript
    assert "Install Latest Main" in javascript
    assert "Code error [Traceback]" in html
    assert "Status: Code error [Traceback]" not in html
    assert "Could not check connection." in javascript
    assert "Status: " not in javascript
    assert "window.onUpdateCodeError" in javascript
    assert "setCodeError(data.message, data.traceback)" in javascript
    assert "window.onUpdateLoadFailed" in javascript
    assert "Could not check for updates" in javascript
    assert "String(entry.number)" in javascript
    assert "entry.title" in javascript
    assert "update-card" in css
    assert 'id="github-link"' in html
    assert "Auto-update channel: Experimental (-E)" in html
    assert "<select" not in html
    assert 'id="sprites-check-progress"' not in html
    assert 'id="developer-status" class="muted hidden"' in html
    assert "normalizeTagSearch" in javascript
    assert ".custom-checkbox:checked + .checkbox-indicator" in css
    assert "height: 24px;" in css
    assert 'id="branch-progress"' in html
    assert 'id="branch-subtabs"' in html
    assert 'data-branch-view="main"' in html
    assert 'data-branch-view="current"' in html
    branch_panel = html[html.index('id="panel-branch"'):html.index('id="panel-releases"')]
    assert branch_panel.index('id="active-branch-title"') < branch_panel.index(
        'id="branch-subtabs"'
    ) < branch_panel.index("Recent Branch Updates")
    assert branch_panel.index("Recent Branch Updates") < branch_panel.index(
        'id="branch-status"'
    ) < branch_panel.index('id="recent-commits"')
    assert "padding-bottom: 180px;" in css[css.index("#panel-developer"):]
    assert "border-bottom-color: var(--accent-blue);" in css
    assert " (Current)" in javascript
    assert " (Last Main)" in javascript
    assert '"commit_date": commit_date' in (
        ADDON / "pyobj" / "update_manager.py"
    ).read_text(encoding="utf-8")
    assert '"main_commit_date": main_commit_date' in (
        ADDON / "pyobj" / "update_manager.py"
    ).read_text(encoding="utf-8")
    assert 'commit_date = state.get("commit_date")' in (
        ADDON / "pyobj" / "update_manager_web.py"
    ).read_text(encoding="utf-8")
    assert 'main_commit_date = state.get("main_commit_date")' in (
        ADDON / "pyobj" / "update_manager_web.py"
    ).read_text(encoding="utf-8")
    assert "Installed Source: " in javascript
    assert "sourceUrl(data.source_type, data.source_name)" in javascript
    assert "sourceLinkTarget(sourceType, sourceName)" in javascript
    assert "sourceLinkLabel(linkTarget.sourceType, linkTarget.sourceName)" in javascript
    assert "sourceLinkLabel(data.source_type, data.source_name)" in javascript
    assert "if (sourceType === 'installed' && updateData)" in javascript
    assert "if (sourceType === 'pr') return `PR #${sourceName}`" in javascript
    assert "target.sourceType === 'pr'" in javascript
    assert "target.sourceType === 'release'" in javascript
    assert "target.sourceType === 'tag'" in javascript
    assert "/commit/${encodeURIComponent(sha)}" in javascript
    assert "event.target.closest('.update-tab-link')" in javascript
    assert "passive: false" in javascript
    assert "activeProgressBranchView" in javascript
    assert "syncBranchProgressVisibility()" in javascript
    assert "selectedBranchView === activeProgressBranchView" in javascript
    assert 'id="releases-progress"' in html
    assert 'id="developer-progress"' in html
    assert 'id="sprites-progress"' in html
    assert "<output>0%</output>" in html
    assert (
        "Update applied successfully. Please restart Anki for changes to take effect."
        in javascript
    )
    assert "sprite_check_up_to_date" in javascript
    assert "openGitHubTarget" in javascript
    assert "install" in javascript and "sourceUrl(sourceType, sourceName)" in javascript
    assert "options.scrollTop = 0" in javascript
    assert "event.stopPropagation()" in javascript
    assert 'id="update-tabs"' not in html
    assert "event.target.closest('.update-tab')" not in javascript
    assert "has-open-dropdown" in javascript
    assert 'class="github-link-text" aria-hidden="true">Star Ankimon (-E) on GitHub</span>' in html
    assert 'title="Star Ankimon' not in html
    assert "width: 260px;" in css
    assert "sprites-snooze-row" in html
    assert 'id="sprites-progress" class="sprite-progress hidden"' in html
    assert 'id="sprites-status"></span><output>0%</output>' in html
    assert "Ready to check for updates" not in html
    assert "border-top: 1px dashed var(--border-main);" in css
    assert ".progress-label span {" in css
    assert "font-weight: 400;" in css
    assert "progress-message-failure" in javascript
    assert "setOperationStatus('', false)" in javascript
    assert "github-link').classList.toggle('hidden', tab === 'sprites')" in javascript
    warning_start = html.index('<blockquote id="developer-warning"')
    warning_end = html.index("</blockquote>", warning_start)
    warning = html[warning_start:warning_end]
    assert "progress file(s) will not be lost" in warning
    assert "pull requests and branches may contain unreviewed code" in warning
    assert "<blockquote id=\"developer-warning\"" in html
    assert ".update-tabs {" not in css
    assert ".update-tab {" not in css
    trigger_focus = css[
        css.index(".custom-select-trigger:focus"):
        css.index(".custom-select-value")
    ]
    assert "outline: none;" in trigger_focus
    assert "box-shadow: none;" in trigger_focus
    assert "border-color: var(--accent-blue);" not in trigger_focus
    assert "Connection rate limited, pika try again soon!" in javascript
    assert "progress-message-rate-limit" in css
    assert "status-text.rate-limit" in css
    assert "release-highlight.error" in css
    assert ".content-scroll {" in css
    assert "overflow-x: hidden;" in css
    assert "max-width: 100%;" in css[css.index(".custom-select-menu {"):css.index(".custom-select-menu .custom-select-search")]
    assert "padding-bottom: 180px;" in css
    assert "padding-bottom: 360px;" not in css
    assert "fetch_source_sha" in (
        ADDON / "pyobj" / "update_manager_web.py"
    ).read_text(encoding="utf-8")
    assert "fetch_pull_request" in (
        ADDON / "pyobj" / "update_manager_web.py"
    ).read_text(encoding="utf-8")
    assert "fetch_commits_between" in (
        ADDON / "pyobj" / "update_manager.py"
    ).read_text(encoding="utf-8")
    assert "switchBranchView" in javascript
    assert "dataset.branchView" in javascript
    branch_switch = javascript[
        javascript.index("function switchBranchView"):
        javascript.index("function renderReleases")
    ]
    assert "const gitInfo = updateData.git_info || {};" in branch_switch
    assert "if (updateData.git_clone)" in branch_switch
    assert "def consume_api_error()" in (
        ADDON / "pyobj" / "update_manager.py"
    ).read_text(encoding="utf-8")
    bridge = (ADDON / "pyobj" / "update_manager_web.py").read_text(
        encoding="utf-8"
    )
    assert "def openGitHubTarget(self, target)" in bridge
    assert "updater.order_tags_by_release_order" in bridge
    assert 'url.host().lower() != "github.com"' in bridge
    assert "_sprite_check_up_to_date = False" in bridge
    assert "def setBranchSnooze(self, snoozed)" in bridge
    assert "def setSpriteSnooze(self, snoozed)" in bridge
    assert "bridge.setBranchSnooze(event.target.checked" in javascript
    assert "bridge.setSpriteSnooze(event.target.checked" in javascript
    shell = (ADDON / "ankimon_items_web" / "shop_obj.py").read_text(
        encoding="utf-8"
    )
    assert ".update-manager-heading" in shell
    assert ".update-manager-logo" in shell
    assert "openGitHub" in javascript


def test_shell_routes_update_manager_from_help_menu():
    shell = (ADDON / "ankimon_items_web" / "shop_obj.py").read_text(
        encoding="utf-8"
    )
    menu = (ADDON / "menu_buttons.py").read_text(encoding="utf-8")

    assert 'SCREEN_UPDATE = "update_manager"' in shell
    assert "def openUpdateManager(self)" in shell
    assert "update_manager" in shell
    assert '_open_shell_at("update_manager")' in menu
    assert "from .pyobj.update_dialog import UpdateDialog" not in menu
