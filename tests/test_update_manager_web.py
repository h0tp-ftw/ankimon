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
        self.update_manager_icons = []
        self.inside_update_manager = False
        self.nav_screens = []
        self.local_resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-screen") == "update_manager":
            self.update_manager_links += 1
            self.inside_update_manager = True
        if tag == "img" and self.inside_update_manager:
            self.update_manager_icons.append(attrs.get("src"))
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

    def handle_endtag(self, tag):
        if tag == "button" and self.inside_update_manager:
            self.inside_update_manager = False


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
        assert len(parser.update_manager_icons) == 1, relative_path
        assert parser.update_manager_icons[0].endswith("/up-grade.png"), relative_path
        assert parser.nav_screens.index("update_manager") < parser.nav_screens.index(
            "backup_manager"
        ), relative_path
        assert all((page.parent / item).exists() for item in parser.local_resources)


def test_update_manager_markup_exposes_required_panels_and_statuses():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    assert 'src="../../user_files/sprites/items/up-grade.png"' in html
    assert 'src="../../user_files/sprites/items/exp-share.png"' not in html
    assert 'id="pr-search"' in html
    assert 'href="update_manager.css?v=20261113.2"' in html
    assert 'src="update_manager.js?v=20261113.2"' in html
    assert 'id="pr-pagination"' in html
    assert 'id="pr-page-summary"' in html
    assert 'id="pr-page-limit"' in html
    assert 'id="pr-page-prev"' in html and "<svg" in html
    assert 'id="pr-page-next"' in html and 'viewBox="0 0 24 24"' in html
    assert "Max 100 PRs shown" in html
    assert "Showing PR #" in javascript
    assert "const PR_PAGE_SIZE = 50;" in javascript
    assert (
        "entries.slice(prPage * PR_PAGE_SIZE, (prPage + 1) * PR_PAGE_SIZE)"
        in javascript
    )
    assert "querySelector('.custom-select-options').scrollTop = 0;" in javascript
    assert "status-text.neutral" in css
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
    assert "font-style: italic;" not in css[css.index(".lost-commits-more"):css.index(".btn:disabled")]
    assert 'id="branch-progress"' in html
    assert 'id="developer-subtabs"' in html
    assert 'data-developer-view="source"' in html
    assert 'data-developer-view="main"' in html
    assert 'data-developer-view="current"' in html
    assert 'id="release-subtabs"' in html
    assert 'data-release-view="releases">Release</button>' in html
    assert 'data-release-view="releases"' in html
    assert 'data-release-view="pre-release"' in html
    assert 'id="panel-branch"' not in html
    sidebar = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>", html.index('<nav class="sidebar-nav">'))]
    assert [part.split('"')[0] for part in sidebar.split('data-tab="')[1:]] == [
        "releases", "sprites", "developer"
    ]
    developer_panel = html[html.index('id="panel-developer"'):html.index('id="panel-sprites"')]
    assert developer_panel.index("Install code directly from branches, pull requests, or tags.") < developer_panel.index('id="active-branch-title"')
    assert developer_panel.index('id="active-branch-title"') < developer_panel.index("Note:")
    assert developer_panel.index("<blockquote id=\"developer-warning\"") < developer_panel.index('id="developer-subtabs"')
    assert developer_panel.index('id="developer-subtabs"') < developer_panel.index("Install from Source")
    releases_panel = html[html.index('id="panel-releases"'):html.index('id="panel-developer"')]
    pre_release = releases_panel[releases_panel.index('id="release-view-pre-release"'):]
    assert pre_release.index("Recent Branch Updates") < pre_release.index('id="branch-status"')
    assert pre_release.index('id="branch-status"') < pre_release.index('id="recent-commits"')
    assert 'id="branch-snooze"' in pre_release and 'id="branch-update-btn"' in pre_release
    release_panel_start = css.index("#panel-releases,")
    release_panel_padding = css[
        release_panel_start:css.index("}", release_panel_start)
    ]
    assert "padding-bottom: 90px;" in release_panel_padding
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
    assert "progress and configuration will not be lost" in warning
    assert "pull requests and side branches may contain unreviewed code" in warning
    assert "<strong>irreversible</strong>" in warning
    assert "<strong>Please install sources you trust.</strong>" in warning
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
    assert "padding-bottom: 90px;" in css
    assert "padding-bottom: 90px;" in css[css.index(".update-content.has-open-dropdown"):css.index(".code-error")]
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
    assert "dataset.developerView" in javascript
    branch_switch = javascript[
        javascript.index("function switchDeveloperView"):
        javascript.index("function showDeveloperWarningIfNeeded")
    ]
    assert "selectedBranchView = selectedDeveloperView;" in branch_switch
    assert "appendChild(byId('branch-update-view'))" in branch_switch
    assert "Update to Main Now" in javascript
    assert "Main comparison point not recorded" not in javascript
    assert "unknown (not recorded)" not in javascript
    assert "'unknown'" in javascript
    assert "Switch to Latest Release" in javascript
    assert "Updates that will be lost" in javascript
    assert "lostReleaseCommits.slice(0, 100)" in javascript
    assert "...And more." in javascript
    assert "Unlike main, these are collaborator branches and can be unreleased." in javascript
    assert (
        "Anyone can open a pull request; this code is unreviewed and unreleased. "
        "Installing it runs unverified code on your device. It may or may not "
        "behave as expected, and can even break the add-on. Please proceed if you "
        "trust this PR. Use at your own risk."
    ) in javascript
    assert "unreleased — installing it runs unverified code" not in javascript
    assert "warning-modal" in css
    assert "Do not show this again" in javascript
    assert "'Developer Warning'" in javascript
    assert "the code might not do as their description says" in javascript
    assert "Pull requests and side branches may be experiments or work in progress." in javascript
    assert "row.className = 'check-row developer-warning-checkbox';" in javascript
    assert "indicator.className = 'checkbox-indicator';" in javascript
    assert "escapeCloses = true" in javascript
    assert "false,\n\t\t\t);" in javascript
    assert "showDeveloperWarningIfNeeded();" in javascript
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
    assert "def getDeveloperWarningHidden(self)" in bridge
    assert "def setDeveloperWarningHidden(self, hidden)" in bridge
    assert '"hide_developer_warning"' in bridge
    assert "database = services.db" in bridge
    assert "database.set_user_data(\"hide_developer_warning\", 1 if hidden else 0)" in bridge
    assert "commit_cache = {}" in bridge
    assert "commit_cache[key] = (" in bridge
    assert "updater.consume_api_error()" in bridge
    assert '"main_commits_error": main_commits_error' in bridge
    assert "bleeding_edge_release" in bridge
    assert "bridge.setBranchSnooze(event.target.checked" in javascript
    assert "bridge.setSpriteSnooze(event.target.checked" in javascript
    shell = (ADDON / "ankimon_items_web" / "shop_obj.py").read_text(
        encoding="utf-8"
    )
    assert ".update-manager-heading" in shell
    assert ".update-manager-logo" in shell
    assert "openGitHub" in javascript
    assert "Connection rate limited! Pika try again soon." in javascript
    assert "Connection rate limited, Pika try again!" in javascript
    assert "No PRs found" in javascript
    assert "No branches found" in javascript
    assert "No tags found" in javascript
    assert "Could not load PRs" in javascript
    assert "Could not load branches" in javascript
    assert "Could not load tags" in javascript
    assert "targetTrigger.disabled = noSources;" in javascript
    assert "entries: (developerData.branches || []).filter((entry) => entry.name !== 'main')" in javascript
    assert "{ value: 'main', label: 'Latest Main Branch' }" not in javascript
    assert "{ value: 'branch', label: 'Side Branch' }" in javascript
    assert "status.textContent = 'Update available!';" in javascript
    assert "status.textContent = 'Up to date!';" in javascript
    assert "One click to enjoy the latest bleeding edge updates, pre-release." in html
    assert 'id="source-rate-limit-status"' in html
    assert "developer-subtabs').addEventListener('wheel'" in javascript
    assert "event.deltaY > 0 ? 1 : -1" in javascript
    assert "gap: 14px;" in css[css.index("#branch-update-view"):css.index(".status-text")]
    assert "version = `| PR #${state.source_name}`;" in javascript
    assert "version = `| Branch ${state.source_name}`;" in javascript
    assert "const externalPrOrBranch = hasBranchViews" in javascript
    assert "branchCanUpdate = true;" in javascript
    assert "user_files/, which stores your Ankimon data." in javascript
    assert javascript.count(
        "Please check if the source modifies user_files/, which stores your Ankimon data."
    ) == 1
    assert "const developerSourceInstall = progressPanel === 'developer' &&" in javascript
    assert "isPullRequestOrSideBranch" in javascript
    assert "Your Pokémon data, settings, and sprites will be preserved." not in javascript
    assert "Your Ankimon progress and configuration will not be lost." in javascript
    assert "confirm-update-warning" in javascript
    assert "risk.textContent = riskText;" in javascript
    assert "trustSources.textContent = 'Please proceed with sources you trust.'" in javascript
    assert "irreversible.textContent = 'irreversible';" in javascript
    assert "warning.endsWith(riskText)" in javascript
    assert "margin-bottom: 3px;" in css[css.index(".confirm-update-warning"):css.index(".update-modal-card .modal-warning-text")]
    assert "user-files-warning" in javascript
    assert ".update-modal-card .user-files-warning" in css
    assert "--update-deep-warning: #b26a00;" in css


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
