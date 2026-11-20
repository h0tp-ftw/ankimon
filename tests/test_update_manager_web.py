import importlib.util
import io
import sys
import types
import urllib.error
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest


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
    assert 'href="update_manager.css?v=20261121.2"' in html
    assert 'src="update_manager.js?v=20261121.2"' in html
    assert 'id="developer-warning-info"' in html
    assert 'aria-label="Show developer warning"' in html
    info_icon_start = html.index('id="developer-warning-info"')
    info_icon_end = html.index("</svg>", info_icon_start)
    info_icon = html[info_icon_start:info_icon_end]
    assert 'viewBox="0 0 24 24"' in info_icon
    assert '<circle cx="12" cy="12" r="9.25"' in info_icon
    assert 'stroke-linecap="round"' in info_icon
    assert 'id="pr-pagination"' in html
    assert 'id="pr-page-prev"' in html and "<svg" in html
    assert 'id="pr-page-next"' in html and 'viewBox="0 0 24 24"' in html
    # The source dropdown's footer is the two page arrows and nothing else:
    # no "Showing PR #x to #y" range and no "Max 100 PRs shown" limit line.
    pr_footer_start = html.index('<div id="pr-pagination"')
    pr_footer = html[pr_footer_start:html.index('<button id="developer-install-btn"', pr_footer_start)]
    assert pr_footer.count("<button") == 2
    assert "<span" not in pr_footer
    for removed in ('id="pr-page-summary"', 'id="pr-page-limit"', "Max 100 PRs shown"):
        assert removed not in html
    for removed in (
        "pr-page-summary", "pr-page-limit", "Showing PR #", "Max 100 PRs shown",
        "Showing tag ${start} to ${end}", "Max 100 tags shown",
    ):
        assert removed not in javascript
    assert "#pr-page-summary" not in css and "#pr-page-limit" not in css
    pr_footer_css = css[css.index("#pr-pagination {"):]
    pr_footer_css = pr_footer_css[:pr_footer_css.index("}")]
    assert "grid-template-columns: auto;" in pr_footer_css
    assert "justify-content: center;" in pr_footer_css
    # The arrows keep their labels and disabled states for both paged types.
    paging = javascript[
        javascript.index("targetMenu.classList.toggle('paged-menu', showPagination);"):
        javascript.index("const valueFor = (entry) =>")
    ]
    assert "type === 'tag' ? 'Previous tag page' : 'Previous pull request page'" in paging
    assert "type === 'tag' ? 'Next tag page' : 'Next pull request page'" in paging
    assert "byId('pr-page-prev').disabled = sourcePage === 0;" in paging
    assert "byId('pr-page-next').disabled = sourcePage >= pageCount - 1;" in paging
    assert "setText(" not in paging
    # Only the release footer stacks summary / arrows / limit on a narrow window.
    narrow = css[css.index("@media (max-width: 760px)"):]
    assert "#release-pagination {\n\t\tgrid-template-columns: minmax(0, 1fr);" in narrow
    assert "#release-pagination .dropdown-page-controls {\n\t\tgrid-area: controls;" in narrow
    assert "\t.dropdown-pagination {" not in narrow
    assert 'id="release-pagination"' in html
    assert "Max 100 releases shown" in html
    assert 'id="developer-source-progress"' in html
    assert "const DROPDOWN_PAGE_SIZE = 50;" in javascript
    assert (
        "entries.slice(sourcePage * DROPDOWN_PAGE_SIZE, (sourcePage + 1) * DROPDOWN_PAGE_SIZE)"
        in javascript
    )
    assert "entries.slice(\n\t\t\treleasePage * DROPDOWN_PAGE_SIZE" in javascript
    assert "options[type].entries.length > DROPDOWN_PAGE_SIZE" in javascript
    assert "list.length <= DROPDOWN_PAGE_SIZE" in javascript
    assert "Showing release ${first} to ${last}" in javascript
    assert "querySelector('.custom-select-options').scrollTop = 0;" in javascript
    assert "status-text.neutral" in css
    assert "Switch to pre-release" in javascript
    assert "Install Latest Main" not in javascript
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
    assert "Auto-update channel: Experimental</p>" in html
    assert "(-E)" not in html
    assert "<select" not in html
    assert 'id="sprites-check-progress"' not in html
    assert 'id="developer-status" class="muted hidden"' in html
    assert "normalizeTagSearch" in javascript
    assert ".custom-checkbox:checked + .checkbox-indicator" in css
    assert "height: 10px;" in css
    assert "font-style: italic;" not in css[css.index(".lost-commits-more"):css.index(".btn:disabled")]
    assert 'id="pre-release-progress"' in html
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
    # Sub-tabs first, then the install-from-source form, then the Installed
    # source card. The card follows every sub-view (not only Source), and the
    # form's loading / error line stays between the form and the card.
    assert developer_panel.index('id="developer-subtabs"') < developer_panel.index("Install from source")
    assert developer_panel.index("Install from source") < developer_panel.index('id="developer-install-btn"')
    assert developer_panel.index('id="developer-install-btn"') < developer_panel.index('id="active-branch-title"')
    assert developer_panel.index('id="developer-view-current"') < developer_panel.index('id="developer-status"')
    assert developer_panel.index('id="developer-status"') < developer_panel.index('id="active-branch-title"')
    source_view = developer_panel[
        developer_panel.index('id="developer-view-source"'):
        developer_panel.index('id="developer-view-main"')
    ]
    assert "Install from source" in source_view
    assert 'id="active-branch-title"' not in source_view
    # No intro paragraph and no permanent inline warning; the warning lives
    # in the Developer warning pop-up and the install confirmation instead.
    assert "Install code directly from branches" not in html
    assert "developer-intro" not in html and ".developer-intro" not in css
    assert "<blockquote" not in developer_panel
    assert 'id="developer-warning"' not in html
    assert "Note:" not in developer_panel
    releases_panel = html[html.index('id="panel-releases"'):html.index('id="panel-developer"')]
    pre_release = releases_panel[releases_panel.index('id="release-view-pre-release"'):]
    assert pre_release.index("Recent changes") < pre_release.index('id="branch-status"')
    assert pre_release.index('id="branch-status"') < pre_release.index('id="recent-commits"')
    assert 'id="branch-snooze"' in pre_release and 'id="branch-update-btn"' in pre_release
    assert 'id="pre-release-progress"' in pre_release
    release_view = releases_panel[
        releases_panel.index('id="release-view-releases"'):
        releases_panel.index('id="release-view-pre-release"')
    ]
    assert 'id="release-progress"' in release_view
    release_panel_start = css.index("#panel-releases,")
    release_panel_padding = css[
        release_panel_start:css.index("}", release_panel_start)
    ]
    assert "padding-bottom: 90px;" in release_panel_padding
    assert "border-bottom-color: var(--accent-blue);" in css
    # Installed source: a labelled grid (Installed / Pre-release rows against
    # Commit / Date columns) built from real cells, not newline-joined text.
    assert '<th scope="row">Installed</th><td id="installed-commit">' in html
    assert '<td id="commit-date">' in html
    assert '<tr id="pre-release-source-row" class="hidden"><th scope="row">Pre-release</th>' in html
    assert '<th scope="col">Commit</th><th scope="col">Date</th>' in html
    assert 'Last installed <span id="last-update-installed">' in html
    assert "setVisible(byId('pre-release-source-row'), hasBranchViews);" in javascript
    assert ".join('\\n')" not in javascript
    assert " (current)" not in javascript
    assert " (last pre-release)" not in javascript
    # One local-time date format for all three values, invalid dates guarded.
    assert "new Intl.DateTimeFormat('en-GB', {" in javascript
    assert "hour12: false," in javascript
    assert "Number.isNaN(date.getTime()) ? '' : SOURCE_DATE_FORMAT.format(date)" in javascript
    assert "toLocaleString()" not in javascript
    assert "Next update will fetch this" not in javascript
    assert "Perform update to record" not in javascript
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
    assert "Installed source: " in javascript
    assert "Active branch: " not in javascript
    assert "sourceUrl(data.source_type, data.source_name)" in javascript
    assert "sourceLinkTarget(sourceType, sourceName)" in javascript
    assert "sourceLinkLabel(linkTarget.sourceType, linkTarget.sourceName)" in javascript
    assert "sourceLinkLabel(data.source_type, data.source_name)" in javascript
    # A non-Git install of main reads "Pre-release" in the heading; the link
    # still opens the real branch and sourceLinkLabel itself is unchanged.
    heading = javascript[
        javascript.index("'Installed source: '"):
        javascript.index("activeBranchTitle.appendChild(sourceLink);")
    ]
    assert (
        "const preReleaseSource = !data.git_clone && recordedSource.source_type === 'branch' &&\n"
        "\t\t\t(recordedSource.source_name || 'main') === 'main';"
    ) in heading
    assert "if (preReleaseSource || (data.source_type && data.source_name)) {" in heading
    assert (
        "sourceLink.textContent = preReleaseSource\n"
        "\t\t\t\t? 'Pre-release'\n"
        "\t\t\t\t: sourceLinkLabel(data.source_type, data.source_name);"
    ) in heading
    assert "sourceUrl(data.source_type, data.source_name)," in heading
    assert "return sourceName || 'main';" in javascript
    assert "if (sourceType === 'installed' && updateData)" in javascript
    assert "if (sourceType === 'pr') return `PR #${sourceName}`" in javascript
    assert "target.sourceType === 'pr'" in javascript
    assert "target.sourceType === 'release'" in javascript
    assert "target.sourceType === 'tag'" in javascript
    assert "/commit/${encodeURIComponent(sha)}" in javascript
    # The wheel scrolls the page; arrow keys move between tabs instead.
    assert (
        "wireTabKeys(document.querySelector('.sidebar-nav'), '.update-tab-link', "
        "'ArrowUp', 'ArrowDown');"
    ) in javascript
    assert "passive: false" in javascript
    assert "activeProgressBranchView" not in javascript
    assert "syncBranchProgressVisibility()" not in javascript
    assert "activeProgressKey = progressKey;" in javascript
    assert "hostId === 'release-view-pre-release'" in javascript
    assert 'id="release-progress"' in html
    assert 'id="pre-release-progress"' in html
    assert 'id="developer-source-progress"' in html
    assert 'id="developer-main-progress"' in html
    assert 'id="developer-current-progress"' in html
    assert 'id="git-progress"' in html
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
    assert "'--dropdown-scroll-extra'" in javascript
    assert "getBoundingClientRect().bottom" in javascript
    assert "contentScroll.scrollTop = Math.min(contentScroll.scrollTop + scrollBy, maxScroll);" in javascript
    # A quiet, always-visible text link; the visible text is its name.
    assert '<span class="github-link-text">Star on GitHub</span>' in html
    assert 'title="Star Ankimon' not in html
    assert "width: 260px;" not in css
    github_link = css[css.index(".github-link {"):css.index(".installed-source-detail")]
    assert "color: var(--text-muted);" in github_link
    assert "color: var(--text-bright);" in github_link
    assert "outline: 2px solid var(--accent-blue);" in github_link
    assert "box-shadow" not in github_link
    assert "flex-basis" not in github_link
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
    # The Developer warning pop-up (shown when the tab is first opened and
    # from the info button) carries the warning and ends on the bold
    # "Install only from trusted sources."
    popup = javascript[
        javascript.index("function showDeveloperWarningIfNeeded"):
        javascript.index("function renderReleases")
    ]
    assert "'This section contains developer tools and is usually for testing purposes. Installations made here are '" in popup
    assert "irreversible.textContent = 'irreversible';" in popup
    assert "trustSources.textContent = 'Install only from trusted sources.';" in popup
    assert "warning.appendChild(trustSources);" in popup
    warning_built = popup[:popup.index("content.append(warning, row);")]
    assert warning_built.rindex("warning.") == warning_built.index("warning.appendChild(trustSources);")
    assert "'Developer warning'," in popup
    assert "if (tab === 'developer') {\n\t\t\tswitchDeveloperView(selectedDeveloperView);\n\t\t\tshowDeveloperWarningIfNeeded();" in javascript
    assert "byId('developer-warning-info').addEventListener('click', () => {\n\t\tshowDeveloperWarningIfNeeded(true);" in javascript
    assert ".update-tabs {" not in css
    assert ".update-tab {" not in css
    # Keyboard focus on a dropdown trigger shows the accent ring.
    trigger_focus = css[
        css.index(".custom-select-trigger:focus-visible"):
        css.index(".custom-select-value")
    ]
    assert "border-color: var(--accent-blue);" in trigger_focus
    assert "box-shadow: 0 0 0 3px rgba(88, 166, 255, .22);" in trigger_focus
    assert "box-shadow: none;" not in trigger_focus
    assert javascript.count("Connection rate limited, pika try again soon!") == 1
    assert "Connection rate limited! Pika try again soon." not in javascript
    assert "Connection rate limited, Pika try again!" not in javascript
    assert "progress-message-rate-limit" in css
    assert "status-text.rate-limit" in css
    assert "release-highlight.error" in css
    assert ".release-highlight.success" in css
    # Rate limiting is its own tone (yellow bolt), never the red error look,
    # and every re-render of the release label clears it.
    assert ".release-highlight.rate-limit" in css
    assert "latestLabel.classList.add('rate-limit');" in javascript
    assert "latestLabel.classList.remove(...STATUS_TONES);" in javascript
    rate_limit_tone = css[css.index("/* Rate limiting is a wait"):css.index(".commit-list {")]
    assert "--status-tone: #f5c518;" in rate_limit_tone
    assert "M9.6 1 3 9.2h3.9L6 15l6.8-8.4H8.7z" in rate_limit_tone
    assert "status-text.rate-limit" not in css[
        css.index(".status-text.error,\n.status-text.high-risk"):
        css.index("/* Rate limiting is a wait")
    ]
    assert ".content-scroll {" in css
    assert "overflow-x: hidden;" in css
    assert "max-width: 100%;" in css[css.index(".custom-select-menu {"):css.index(".custom-select-menu .custom-select-search")]
    assert "padding-bottom: 90px;" in css
    assert ".update-content.has-open-dropdown" not in css
    assert "#panel-releases.has-open-dropdown" in css
    assert "#panel-developer.has-open-dropdown" in css
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
    assert "Update to pre-release" in javascript
    assert "Update now" in javascript
    assert ": inPreRelease ? 'Recent changes' : 'Recent branch changes'" in javascript
    assert "Main comparison point not recorded" not in javascript
    assert "unknown (not recorded)" not in javascript
    assert "'Unknown'" in javascript
    assert "'Never'" in javascript
    assert "Switch to latest release" in javascript
    assert "You currently have " in javascript
    assert (
        "` installed, which will be removed once you ${replacementVerb}.\\n\\n`"
        in javascript
    )
    assert "createSourceLink(replacingSource, installedLabel)" in javascript
    assert "sourceUrl(target.sourceType, target.sourceName)" in javascript
    assert "function installedExternalSource()" in javascript
    assert "source.sourceType === 'pr'" in javascript
    assert "source.sourceName !== 'main'" in javascript
    assert "sourceType !== 'installed'" in javascript
    assert "sourceType !== 'current'" in javascript
    assert "progressKey !== 'developer-current'" in javascript
    assert "function installActionVerb(buttonLabel)" in javascript
    assert "installActionVerb(byId('latest-release-btn').textContent)" in javascript
    assert "installActionVerb(byId('release-install-btn').textContent)" in javascript
    assert "installActionVerb(byId('developer-install-btn').textContent)" in javascript
    assert "Updates that will be lost" in javascript
    assert "lostReleaseCommits.slice(0, 100)" in javascript
    assert "...And more." in javascript
    assert "These are collaborator branches, separate from the pre-release." in javascript
    assert (
        "Anyone can open a pull request; this code is unreviewed and unreleased. "
        "Installing it runs unverified code on your device. It may or may not "
        "behave as expected, and can even break the add-on. Please proceed if you "
        "trust this PR. Use at your own risk."
    ) in javascript
    assert "unreleased — installing it runs unverified code" not in javascript
    assert "warning-modal" in css
    assert "Do not show this again" in javascript
    assert "'Developer warning'" in javascript
    assert "showDeveloperWarningIfNeeded(true)" in javascript
    assert "checkbox.checked = Boolean(hidden);" in javascript
    assert ".developer-warning-info:hover" in css
    assert "stroke-width: 1.35;" in css[
        css.index(".developer-warning-info svg {"):
        css.index("#source-type-select")
    ]
    assert "color: var(--text-main);" in css[
        css.index(".developer-warning-info {"):
        css.index(".developer-warning-info:hover")
    ]
    assert "color: var(--accent-blue);" in css[
        css.index(".developer-warning-info:hover"):
        css.index(".developer-warning-info:focus-visible")
    ]
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
    assert javascript.count("Connection rate limited, pika try again soon!") == 1
    assert "No PRs found" in javascript
    assert "No branches found" in javascript
    assert "No tags found" in javascript
    assert "Could not load PRs" in javascript
    assert "Could not load branches" in javascript
    assert "Could not load tags" in javascript
    assert "targetTrigger.disabled = noSources;" in javascript
    assert "entries: (developerData.branches || []).filter((entry) => entry.name !== 'main')" in javascript
    assert "{ value: 'main', label: 'Latest Main Branch' }" not in javascript
    assert "{ value: 'branch', label: 'Side branch' }" in javascript
    assert "status.textContent = HIGH_RISK_UPDATE_MESSAGE;" in javascript
    assert "status.textContent = 'Up to date';" in javascript
    assert (
        "Changes made since the last release, highly unstable (only install if "
        "you know what you're doing)."
    ) in html
    assert "bleeding edge" not in html and "bleeding edge" not in javascript
    assert 'id="source-rate-limit-status"' in html
    assert (
        "wireTabKeys(byId('developer-subtabs'), '.branch-subtab', 'ArrowLeft', 'ArrowRight');"
        in javascript
    )
    assert "event.deltaY > 0 ? 1 : -1" not in javascript
    assert "gap: 14px;" in css[css.index("#branch-update-view"):css.index(".status-text")]
    assert "version = `PR #${state.source_name}`;" in javascript
    assert "version = `Branch ${state.source_name}`;" in javascript
    assert "const externalPrOrBranch = hasBranchViews" in javascript
    assert "branchCanUpdate = true;" in javascript
    assert ", which stores your Ankimon data." in javascript
    assert "userFilesLead.textContent = 'Please check if the source modifies user_files/';" in javascript
    assert "dropdown.id === 'release-select'" in javascript
    assert "dropdown.id === 'source-target'" in javascript
    assert "byId('panel-releases')" in javascript
    assert "byId('panel-developer')" in javascript
    assert "calc(90px + var(--dropdown-scroll-extra, 0px))" in css
    assert javascript.count("Please check if the source modifies user_files/") == 1
    assert "const developerSourceInstall = progressKey === 'developer-source' &&" in javascript
    assert "isPullRequestOrSideBranch" in javascript
    assert "Your Pokémon data, settings, and sprites will be preserved." not in javascript
    assert "Your Ankimon progress and configuration will not be lost." in javascript
    assert "confirm-update-warning" in javascript
    assert "risk.textContent = riskText;" in javascript
    assert "trustSources.textContent = 'Install only from trusted sources.'" in javascript
    assert "irreversible.textContent = 'irreversible';" in javascript
    assert "warning.endsWith(riskText)" in javascript
    assert "margin-bottom: 3px;" in css[css.index(".confirm-update-warning"):css.index(".update-modal-card .modal-warning-text")]
    assert "user-files-warning" in javascript
    assert ".update-modal-card .user-files-warning" in css
    assert "--update-deep-warning: #b26a00;" in css


def test_update_manager_treats_unreleased_sources_as_high_risk():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    # The pending-update status says so in red.
    assert (
        "const HIGH_RISK_UPDATE_MESSAGE = 'Update available (high risk)';"
        in javascript
    )
    assert "status.classList.add('high-risk');" in javascript
    assert ".status-text.high-risk" in css
    assert "'Update available!'" not in javascript
    # A button that cannot act is hidden, not relabelled to describe a state;
    # the status line above it gives the reason.
    for state_label in (
        "Already Up to Date",
        "Cannot Fast-forward",
        "Branch Not on Ankimon Repository",
        "No Branch Checked Out",
        "Checkout Has Local Changes",
    ):
        assert state_label not in javascript
    assert "setVisible(branchButton, Boolean(branchAction));" in javascript
    assert "setVisible(latestButton, Boolean(latestAction));" in javascript
    assert "if (branchCanUpdate && !branchAction) branchAction = updateAction;" in javascript
    # The confirm modal repeats the verb of the button that opened it.
    assert "`Confirm ${verb}`," in javascript
    # "Update complete." is bolded; Python's "Update complete!" is still stripped.
    assert "message.match(/^(Update complete\\.)(.*)$/)" in javascript
    assert ".replace(/^Update complete[.!]?\\s*/i, '')" in javascript
    assert "Update complete!" not in javascript
    # The Python side follows the same wording rules: no exclamation marks,
    # and main is "the pre-release" in progress text for a non-Git install.
    pyobj = ADDON / "pyobj"
    updater_source = (pyobj / "update_manager.py").read_text(encoding="utf-8")
    assert 'send_progress(100, 100, "Update complete.")' in updater_source
    assert '"Update complete!"' not in updater_source
    sprite_source = (pyobj / "sprite_updater.py").read_text(encoding="utf-8")
    assert 'f"Successfully updated {total_files} sprites."' in sprite_source
    assert "sprites!\"" not in sprite_source
    bridge_source = (pyobj / "update_manager_web.py").read_text(encoding="utf-8")
    assert 'if source_name == "main" and not self._git_clone:' in bridge_source
    assert '"the latest pre-release"' in bridge_source
    assert 'else "the pre-release"' in bridge_source
    # The label is only ever shown, in "Downloading <label>".
    assert bridge_source.count('source["label"]') == 1
    assert '{"label": source["label"], "warning": warning},' in bridge_source
    assert "`Downloading ${data.label}`" in javascript
    # Every re-render clears the red tone, or it would stick to "Up to date".
    assert "'neutral', 'high-risk'];" in javascript
    assert javascript.count("status.classList.remove(...STATUS_TONES);") == 3
    # The label text is display only, never the thing logic branches on.
    assert "status.textContent ===" not in javascript
    assert "branchCanUpdate && mainUpdateAvailable" in javascript

    # Anything that is not a stable release needs a second confirmation.
    assert "if (target.type === 'release') return false;" in javascript
    # ...and a tag that is itself a published release counts as one.
    assert (
        "return !(target.type === 'tag' && isPublishedReleaseTag(target.name));"
        in javascript
    )
    assert "updateData.releases.some((release) => release.name === name)" in javascript
    assert "is not a stable release" not in javascript
    install = javascript[
        javascript.index("\tfunction install("):
        javascript.index("\tfunction isHighRiskTarget")
    ]
    assert "isHighRiskTarget(resolvedTarget)" in install
    assert (
        "showHighRiskConfirmation(resolvedTarget, linkTarget, replacementVerb, proceed)"
        in install
    )
    assert install.count("startInstall(sourceType, sourceName, progressKey)") == 1
    confirmation = javascript[
        javascript.index("\tfunction showHighRiskConfirmation"):
        javascript.index("\tfunction showReleaseSwitchConfirmation")
    ]
    assert "'High-risk update'" in confirmation
    assert "'danger'," in confirmation
    assert "'Install anyway'" in confirmation
    # A double-click on the first modal's Install must not confirm the second.
    assert "HIGH_RISK_ARM_DELAY_MS," in confirmation
    assert "confirm.disabled = armDelayMs > 0;" in javascript
    assert "clearTimeout(modalArmTimer);" in javascript
    assert ".btn-danger {" in css
    assert ".update-modal-card.danger-modal" in css
    assert ".warning-text.danger-text" in css
    assert 'id="modal-icon"' in html


def test_update_manager_visual_consistency():
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    # Installed-source badge: same typeface as the header, no "|" or "(...)".
    badge = css[
        css.index(".installed-source-detail"):css.index(".top-bar-titles h2")
    ]
    assert "JetBrains Mono" not in badge
    assert "border-radius: 999px;" in badge
    assert "version = 'Pre-release';" in javascript
    assert "version = `Tag ${state.source_name}`;" in javascript
    assert "source.textContent = version;" in javascript
    # Progress blocks and action rows share the cards' 20px content column.
    assert "padding: 14px 20px 16px;" in css[
        css.index("\n.operation-progress {"):css.index(".update-card .operation-progress")
    ]
    assert "padding: 0 20px;" in css[
        css.index(".branch-actions,\n.sprites-actions {"):css.index(".channel-row {")
    ]
    sprite_progress = css[
        css.index("\n.sprite-progress {"):css.index(".sprite-progress.hidden")
    ]
    assert "padding-top: 14px;" in sprite_progress
    assert "calc(100% - 8px)" not in css
    # The bar takes the colour of its message.
    assert "progressContainer.dataset.result = result;" in javascript
    assert '[data-result="complete"] progress::-webkit-progress-value' in css
    # A reload keeps the shared branch view under the tab being looked at.
    reload = javascript[
        javascript.index("window.onUpdateData"):javascript.index("window.onDeveloperData")
    ]
    assert "selectedReleaseView === 'pre-release'" in reload
    assert "switchReleaseView('pre-release');" in reload
    # Both view switches re-sync the shared branch button after rendering, so
    # its enabled state always matches the view just shown.
    for function in ("function switchReleaseView", "function switchDeveloperView"):
        body = javascript[javascript.index(function):]
        body = body[:body.index("\n\t}\n")]
        assert body.rstrip().endswith("updateButtonStates();"), function
        assert body.index("renderBranch(updateData);") < body.rindex("updateButtonStates();"), function
    # Installed-source grid: a SHA never breaks; a date stays on one line
    # where it fits and otherwise wraps only after its comma.
    source_cells = css[css.index(".source-details td {"):]
    source_cells = source_cells[:source_cells.index("}")]
    assert "overflow-wrap: normal;" in source_cells
    assert "white-space: nowrap;" in source_cells
    assert "overflow-wrap: anywhere;" not in source_cells
    date_cells = css[css.index("#commit-date,\n#pre-release-commit-date {"):]
    date_cells = date_cells[:date_cells.index("}")]
    assert "white-space: normal;" in date_cells
    assert "overflow-wrap" not in date_cells
    # AGENTS.md: blur causes Windows DWM flicker under QWebEngine.
    assert "backdrop-filter" not in css


def test_update_manager_keyboard_and_focus():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    # Tabs: the wheel never switches them, so it always scrolls the page.
    for row in ("sidebar-nav", "developer-subtabs", "release-subtabs"):
        assert f"{row}').addEventListener('wheel'" not in javascript
    assert javascript.count("addEventListener('wheel'") == 1  # dropdown menu only
    assert "event.deltaY > 0 ? 'pre-release' : 'releases'" not in javascript
    # Arrow keys per row, Home/End, wrapping, hidden tabs skipped, focus first.
    for call in (
        "wireTabKeys(document.querySelector('.sidebar-nav'), '.update-tab-link', 'ArrowUp', 'ArrowDown');",
        "wireTabKeys(byId('release-subtabs'), '.branch-subtab', 'ArrowLeft', 'ArrowRight');",
        "wireTabKeys(byId('developer-subtabs'), '.branch-subtab', 'ArrowLeft', 'ArrowRight');",
    ):
        assert call in javascript
    tab_keys = javascript[
        javascript.index("function wireTabKeys"):javascript.index("function showToast")
    ]
    assert "[previousKey]: tabs[(index - 1 + tabs.length) % tabs.length]," in tab_keys
    assert "[nextKey]: tabs[(index + 1) % tabs.length]," in tab_keys
    assert "Home: tabs[0]," in tab_keys and "End: tabs[tabs.length - 1]," in tab_keys
    assert ".filter((tab) => !tab.disabled && isShown(tab));" in tab_keys
    assert tab_keys.index("target.focus();") < tab_keys.index("target.click();")
    assert "event.preventDefault();" in tab_keys
    # The selected tab is exposed, in the markup and from every switch.
    assert "button.setAttribute('aria-current', 'page');" in javascript
    assert "button.removeAttribute('aria-current');" in javascript
    assert "button.setAttribute('aria-pressed', String(active));" in javascript
    assert 'aria-current="page" data-tab="releases"' in html
    assert 'aria-pressed="true" data-release-view="releases"' in html
    assert 'aria-pressed="false" data-release-view="pre-release"' in html
    assert 'aria-pressed="true" data-developer-view="source"' in html
    assert html.count('aria-pressed="false" data-developer-view=') == 2
    for function, call in (
        ("function switchTab", "markSidebarTab(button, button.dataset.tab === tab);"),
        ("function switchDeveloperView", "markSubtab(button, button.dataset.developerView === selectedDeveloperView);"),
        ("function switchReleaseView", "markSubtab(button, button.dataset.releaseView === selectedReleaseView);"),
    ):
        body = javascript[javascript.index(function):]
        body = body[:body.index("\n\t}\n")]
        assert call in body, function
    assert "button.classList.toggle('active', button.dataset" not in javascript
    assert "markSidebarTab(button, button.classList.contains('active'));" in javascript
    assert "markSubtab(button, button.classList.contains('active'));" in javascript
    assert ".branch-subtab:focus-visible {" in css
    assert ".update-tab-link:focus-visible {" in css
    tab_focus = css[css.index(".branch-subtab:focus-visible {"):css.index(".subtab-panel {")]
    assert tab_focus.count("outline: 2px solid var(--accent-blue);") == 2

    # Dropdowns: a visible ring on the trigger and the search box.
    search_focus = css[
        css.index('.update-content input[type="search"]:focus-visible'):
        css.index(".custom-select {")
    ]
    assert "border-color: var(--accent-blue);" in search_focus
    assert "box-shadow: 0 0 0 3px rgba(88, 166, 255, .22);" in search_focus
    assert 'input[type="search"]:focus {' not in css
    assert "border-color: var(--border-main);\n\tbox-shadow: none;" not in css
    assert ".custom-select-option:hover,\n.custom-select-option.is-active," in css
    dropdown = javascript[
        javascript.index("function dropdownOptions"):javascript.index("function setDropdownLabel")
    ]
    assert "option.scrollIntoView({ block: 'nearest' });" in dropdown
    assert "['ArrowDown', 'Enter', ' '].includes(event.key)" in dropdown
    assert "event.key === 'ArrowUp'" in dropdown
    assert "event.key === 'Home' || event.key === 'End'" in dropdown
    assert "event.key === 'Escape'" in dropdown
    # Enter picks from the trigger or the search box only; Space types there.
    assert "onTrigger || (event.key === 'Enter' && event.target === search)" in dropdown
    assert "options[current].click();" in dropdown
    assert dropdown.count("trigger.setAttribute('aria-expanded', 'true');") == 1
    escape = dropdown[dropdown.index("event.key === 'Escape'"):]
    assert escape.index("closeDropdown(dropdown);") < escape.index("trigger.focus();")
    # The highlight survives a re-render (search, paging, reload) by value.
    assert "option.dataset.value = entry.value;" in javascript
    assert "dropdown.dataset.activeValue" in dropdown
    render = javascript[
        javascript.index("function renderOptions"):javascript.index("function currentBusy")
    ]
    assert "restoreHighlight(dropdown);" in render
    # ...except typing: a new search highlights its first match. Paging
    # handlers leave the remembered value alone.
    release_search = javascript[
        javascript.index("byId('release-select').querySelector('.custom-select-search')\n"):
        javascript.index("byId('pr-search').addEventListener('input'")
    ]
    assert "byId('release-select').dataset.activeValue = '';" in release_search
    assert release_search.index("dataset.activeValue = '';") < release_search.index("renderReleaseOptions();")
    pr_search = javascript[
        javascript.index("byId('pr-search').addEventListener('input'"):
        javascript.index("byId('pr-page-prev').addEventListener('click'")
    ]
    assert "byId('source-target').dataset.activeValue = '';" in pr_search
    assert pr_search.index("dataset.activeValue = '';") < pr_search.index("renderDeveloperTargets(false);")
    assert javascript.count("dataset.activeValue = '';") == 2
    assert "dropdown.querySelector('.custom-select-trigger').focus();" in render
    # A pick marks itself selected at once, so the menu reopens on it.
    assert render.index("item.setAttribute('aria-selected', String(item === option));") < render.index("onSelect(entry);")
    # closeDropdown also runs on outside clicks; it must never steal focus.
    close_dropdown = javascript[
        javascript.index("function closeDropdown("):javascript.index("function syncDropdownScrollSpace")
    ]
    assert ".focus()" not in close_dropdown
    assert "dropdown.querySelector('.custom-select-trigger').setAttribute('aria-expanded', 'false');" in close_dropdown

    # Modal: focus is trapped in the card and the page behind is inert.
    modal = javascript[
        javascript.index("function modalControls"):javascript.index("function setCodeError")
    ]
    assert "!element.disabled && isShown(element)" in modal
    assert "if (event.key !== 'Tab') return;" in modal
    assert "(index + step + controls.length) % controls.length" in modal
    assert "appContainer.setAttribute('inert', '');" in modal
    assert modal.count("appContainer.removeAttribute('inert');") == 1
    close = modal[modal.index("const close = () => {"):modal.index("confirm.onclick = () => {")]
    assert close.index("removeAttribute('inert')") < close.index("restoreFocusAfterModal(opener);")
    # The opener gets focus back, or a stable fallback when it is gone.
    restore = modal[modal.index("function restoreFocusAfterModal"):modal.index("function showModal")]
    for check in (
        "element.isConnected",
        "!element.disabled",
        "isShown(element)",
        "panel.querySelector('.branch-subtab.active')",
        "document.querySelector('.update-tab-link.active')",
    ):
        assert check in restore
    # A modal shown over another takes over its opener and drops its handler.
    assert "opener = modalSession.opener;" in modal
    assert "modalSession.detach();" in modal
    assert "if (event.key === 'Escape' && escapeCloses) {" in modal
    assert "confirm.disabled = armDelayMs > 0;" in modal
    # The trap reaches the source link in the message; it gets the same ring.
    link_focus = css[css.index(".source-link:focus-visible {"):css.index(".modal-actions {")]
    assert "outline: 2px solid var(--accent-blue);" in link_focus
    # On the 6/8/12px radius scale.
    assert "border-radius: 6px;" in link_focus
    assert "border-radius: 3px;" not in link_focus



def test_update_manager_checking_state_matches_the_result_line():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    # Before the first result, the release and branch lines say "Checking"
    # as a status line: no dots, and a spinner in the icon slot.
    assert (
        '<p id="latest-release-label" class="release-highlight checking">Checking</p>'
        in html
    )
    assert (
        '<p id="branch-status" class="status-text checking" role="status" '
        'aria-live="polite">Checking</p>'
    ) in html
    # Every other loading placeholder keeps its word and drops the dots.
    assert "Checking..." not in html and "Loading..." not in html
    assert "Checking branch" not in html
    assert '<p id="installed-version">Installed: Loading</p>' in html
    assert "Checking for changes</li>" in html
    assert "setDeveloperStatus('Loading developer options');" in javascript
    assert "Loading developer options..." not in javascript
    assert "setOperationStatus('Checking for updates');" in javascript
    assert "Checking for updates..." not in javascript
    # The checking class is a status tone, so every result clears it; the
    # release label is cleared from the same list.
    assert (
        "const STATUS_TONES = ['checking', 'success', 'warning', 'error', "
        "'rate-limit', 'neutral', 'high-risk'];"
    ) in javascript
    assert "latestLabel.classList.remove(...STATUS_TONES);" in javascript
    # Same font size, weight, line height and 15px icon slot as the results.
    line = css[css.index(".status-text.checking,\n.status-text.success,"):]
    line = line[:line.index("}")]
    assert ".release-highlight.checking," in line
    for declaration in ("font-size: .9rem;", "font-weight: 400;", "line-height: 1.5;"):
        assert declaration in line
    icon = css[css.index(".status-text.checking::before,\n.status-text.success::before,"):]
    icon = icon[:icon.index("}")]
    assert ".release-highlight.checking::before," in icon
    assert "width: 15px;" in icon and "height: 15px;" in icon
    # The spinner: a ring with one muted arc, turning, still under
    # reduced motion.
    spinner = css[css.index("/* Still checking:"):css.index("/* Rate limiting is a wait")]
    assert "border: 2px solid var(--border-main);" in spinner
    assert "border-top-color: var(--text-muted);" in spinner
    assert "border-radius: 50%;" in spinner
    assert "animation: status-checking-spin .8s linear infinite;" in spinner
    assert "transform: rotate(360deg);" in spinner
    reduced = spinner[spinner.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".release-highlight.checking::before" in reduced
    assert "animation: none;" in reduced


def test_update_manager_sprite_check_shows_status_and_counts():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")

    # A status line, then a list of counts read as "13 New sprites".
    result = html[html.index('<div id="sprites-result"'):html.index('<div id="sprites-progress"')]
    assert 'class="sprites-result hidden" role="status" aria-live="polite"' in result
    assert '<p id="sprites-result-status" class="status-text"></p>' in result
    assert (
        '<ul id="sprites-counts" class="sprite-counts hidden" aria-label="Sprite changes">'
        in result
    )
    for key, label in (("added", "New"), ("modified", "Changed"), ("deleted", "Removed")):
        assert (
            f'<li class="sprite-count" data-count="{key}"><span class="sprite-count-value">0</span> '
            f'<span class="sprite-count-label">{label}<span class="visually-hidden"> sprites</span></span></li>'
        ) in result
    # The counts are never a sentence, and never inside the progress message.
    for sentence in ("New sprites:", "Changed sprites:", "Sprites to remove"):
        assert sentence not in javascript
    checked = javascript[
        javascript.index("window.onSpritesChecked = (result) => {"):
        javascript.index("window.onSpriteProgress = (data) => {")
    ]
    assert "setSpriteProgress(" not in checked
    assert "setSpriteResult('Sprites are up to date', 'success');" in checked
    assert "setSpriteResult('Sprite update available', 'warning', {" in checked
    assert "result.rate_limited ? 'rate-limit' : 'error'," in checked
    assert "rateLimited ? 'rate-limit' : 'error'," in checked
    show = javascript[
        javascript.index("\tfunction setSpriteResult("):
        javascript.index("\tfunction setDeveloperStatus(")
    ]
    assert "statusLine.classList.remove(...STATUS_TONES);" in show
    assert "item.classList.toggle('is-zero', value === 0);" in show
    # Nothing to change: only the status line shows.
    assert "setVisible(countList, total > 0);" in show
    # The result replaces the progress block; a new check or an install
    # clears it so the real progress bar takes over again.
    assert "if (message) setVisible(byId('sprites-progress'), false);" in show
    assert javascript.count("setSpriteResult('');") == 3
    # Numbers in tabular figures: non-zero bright, zero greyed.
    value = css[css.index(".sprite-count-value {"):]
    value = value[:value.index("}")]
    assert "color: var(--text-bright);" in value
    assert "font-variant-numeric: tabular-nums;" in value
    zero = css[css.index(".sprite-count.is-zero .sprite-count-value {"):]
    zero = zero[:zero.index("}")]
    assert "color: var(--text-muted);" in zero
    assert "font-weight: 500;" in zero
    label = css[css.index(".sprite-count-label {"):]
    label = label[:label.index("}")]
    assert "font-size: .76rem;" in label
    counts = css[css.index(".sprite-counts {"):]
    counts = counts[:counts.index("}")]
    assert "flex-wrap: wrap;" in counts
    assert ".visually-hidden {" in css

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


def test_update_manager_looks_up_a_pull_request_by_number():
    html = (UPDATE_PAGE / "update_manager.html").read_text(encoding="utf-8")
    css = (UPDATE_PAGE / "update_manager.css").read_text(encoding="utf-8")
    javascript = (UPDATE_PAGE / "update_manager.js").read_text(encoding="utf-8")
    bridge_source = (ADDON / "pyobj" / "update_manager_web.py").read_text(
        encoding="utf-8"
    )

    # A number ("1055" or "#1055") the loaded list lacks is looked up once
    # typing pauses, through the bridge, and only one request per number.
    assert "const PR_LOOKUP_DELAY_MS = 350;" in javascript
    assert ".match(/^#?(\\d{1,12})$/);" in javascript
    schedule = javascript[
        javascript.index("function schedulePullRequestLookup()"):
        javascript.index("function pullRequestLookupNotice()")
    ]
    assert "clearTimeout(prLookupTimer);" in schedule
    assert "}, PR_LOOKUP_DELAY_MS);" in schedule
    assert "if (!number || prLookupsInFlight.has(number)) return;" in schedule
    assert schedule.index("prLookupsInFlight.add(number);") < schedule.index(
        "bridge.lookupPullRequest(number);"
    )
    to_look_up = javascript[
        javascript.index("function pullRequestNumberToLookUp()"):
        javascript.index("function schedulePullRequestLookup()")
    ]
    assert "prLookupOutcomes.has(number) || isListedPullRequest(number)" in to_look_up
    # Partial matches still filter the loaded list as before.
    assert (
        "return String(entry.number).toLocaleLowerCase().includes(numberQuery) ||"
        in javascript
    )
    # Only the search box starts a lookup; a reload or a page change never does.
    assert javascript.count("schedulePullRequestLookup();") == 1
    pr_search = javascript[
        javascript.index("byId('pr-search').addEventListener('input'"):
        javascript.index("byId('pr-page-prev').addEventListener('click'")
    ]
    assert "prLookupFailure = null;" in pr_search
    assert pr_search.index("renderDeveloperTargets(false);") < pr_search.index(
        "schedulePullRequestLookup();"
    )
    source_type = javascript[
        javascript.index("function renderSourceTypes()"):
        javascript.index("function normalizeTagSearch")
    ]
    assert "clearTimeout(prLookupTimer);" in source_type

    # Found pull requests sit beside the loaded list, one entry per number,
    # and everything that lists or installs a pull request reads both.
    entries = javascript[
        javascript.index("function pullRequestEntries()"):
        javascript.index("function isListedPullRequest")
    ]
    assert "!loadedNumbers.has(String(entry.number))" in entries
    assert "return [...found, ...loaded];" in entries
    assert "pr: { label: 'Pull request:', entries: pullRequestEntries() }," in javascript
    assert "? pullRequestEntries().find((pr) => String(pr.number) === value)" in javascript
    assert "(developerData.prs || []).find(" not in javascript
    assert "const defaultEntry = (type === 'pr' && (developerData.prs || [])[0]) ||" in javascript

    # The reply: kept for the session, shown only for the current search.
    reply = javascript[
        javascript.index("window.onPullRequestLookup = (data) => {"):
        javascript.index("window.onDeveloperLoadFailed")
    ]
    stale = "if (selectedSourceType !== 'pr' || searchedPullRequestNumber() !== number) return;"
    assert stale in reply
    assert reply.index("prLookupsInFlight.delete(number);") < reply.index(stale)
    assert reply.index("lookedUpPrs.set(number, data.pr);") < reply.index(stale)
    assert reply.index("prLookupOutcomes.set(number, status);") < reply.index(stale)
    assert "['closed', 'merged', 'not_found'].includes(status)" in reply
    assert reply.index(stale) < reply.index("prLookupFailure = { number, status };")
    assert reply.rstrip().endswith("renderDeveloperTargets(false);\n\t};")

    # The status line above the options, in the page's status-line tones.
    notice = javascript[
        javascript.index("function pullRequestLookupNotice()"):
        javascript.index("function showOptionsNotice")
    ]
    for line in (
        "return { text: `#${number} was merged`, tone: 'neutral' };",
        "return { text: `#${number} is closed`, tone: 'neutral' };",
        "return { text: `No pull request #${number}`, tone: 'neutral' };",
        "return { text: RATE_LIMIT_MESSAGE, tone: 'rate-limit' };",
        "text: 'Could not reach GitHub. Check your connection and try again.',\n\t\t\t\t\ttone: 'error',",
        "text: `GitHub could not look up #${number}. Please try again later.`,\n\t\t\t\ttone: 'warning',",
        "return { text: `Looking up #${number}`, tone: 'checking' };",
    ):
        assert line in notice, line
    assert "Looking up #${number}..." not in javascript
    show = javascript[
        javascript.index("function showOptionsNotice"):
        javascript.index("function renderDeveloperTargets")
    ]
    assert "line.className = `custom-select-notice status-text ${notice.tone}`;" in show
    # Not an option: the keyboard handling (dropdownOptions) never lands on it.
    assert "custom-select-option " not in show and "custom-select-option'" not in show
    assert "setAttribute('role', 'option')" not in show
    assert "return [...dropdown.querySelectorAll('.custom-select-option')];" in javascript
    assert "if (empty) empty.remove();" in show
    assert "options.prepend(line);" in show
    assert "if (lookupNotice) showOptionsNotice(targetDropdown, lookupNotice);" in javascript
    # 'checking' is the spinner the status lines already have, still under
    # reduced motion; the notice keeps the options' inset.
    assert "const STATUS_TONES = ['checking'," in javascript
    notice_css = css[css.index(".custom-select-options .custom-select-notice {"):]
    notice_css = notice_css[:notice_css.index("}")]
    assert "padding: 7.2px 10px;" in notice_css
    assert ".status-text.checking::before,\n\t.release-highlight.checking::before {\n\t\tanimation: none;" in css
    # Screen readers hear the line through one live region that stays put.
    assert (
        '<input id="pr-search" class="custom-select-search" type="search" '
        'placeholder="Search by number or title" '
        'aria-label="Search update sources">\n\t\t\t\t\t\t\t\t\t'
        '<p id="pr-lookup-status" class="visually-hidden" role="status" '
        'aria-live="polite"></p>'
    ) in html
    assert "const lookupStatus = byId('pr-lookup-status');" in javascript

    # Python: a pyqtSlot replying through window.onPullRequestLookup, off the
    # UI thread, without marking the page busy.
    assert "@pyqtSlot(str, result=\"QVariant\")\n    def lookupPullRequest(self, number):" in bridge_source
    assert '"onPullRequestLookup"' in bridge_source
    assert 'f"pr-lookup:{number}", worker, done, failed, announce=False' in bridge_source


# --- the bridge at runtime ------------------------------------------------------
#
# Loaded from source with aqt, PyQt6 and the service registry stubbed, the way
# tests/test_update_manager.py loads the updater: sibling tests replace
# sys.modules['Ankimon'] and friends at import time, so every key touched is
# snapshotted and restored, and nothing leaks into them.


def _load_update_manager_bridge():
    keys = (
        "aqt", "aqt.operations", "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui",
        "Ankimon", "Ankimon.pyobj", "Ankimon.resources", "Ankimon.services",
        "Ankimon.pyobj.update_manager", "Ankimon.pyobj.update_manager_web",
    )
    saved = {key: sys.modules.get(key) for key in keys}
    try:
        aqt = types.ModuleType("aqt")
        aqt.mw = None
        operations = types.ModuleType("aqt.operations")
        operations.QueryOp = object
        qt_core = types.ModuleType("PyQt6.QtCore")
        qt_core.QObject = object
        qt_core.QUrl = object
        qt_core.Qt = types.SimpleNamespace()
        qt_core.pyqtSlot = lambda *_args, **_kwargs: (lambda function: function)
        qt_gui = types.ModuleType("PyQt6.QtGui")
        qt_gui.QDesktopServices = object
        registry = types.ModuleType("Ankimon.services")
        registry.services = types.SimpleNamespace(db=None)
        sys.modules.update({
            "aqt": aqt,
            "aqt.operations": operations,
            "PyQt6": types.ModuleType("PyQt6"),
            "PyQt6.QtCore": qt_core,
            "PyQt6.QtGui": qt_gui,
            "Ankimon.services": registry,
        })
        for name, path in (("Ankimon", ADDON), ("Ankimon.pyobj", ADDON / "pyobj")):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
        for name, relative in (
            ("Ankimon.resources", "resources.py"),
            ("Ankimon.pyobj.update_manager", "pyobj/update_manager.py"),
            ("Ankimon.pyobj.update_manager_web", "pyobj/update_manager_web.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, ADDON / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules["Ankimon.pyobj.update_manager_web"]
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module


web = _load_update_manager_bridge()
updater = web.updater


class _QueryOp:
    """Anki's QueryOp, run to completion as soon as it is started."""

    def __init__(self, parent, op, success):
        self.op = op
        self.success = success
        self.on_failure = None

    def failure(self, callback):
        self.on_failure = callback
        return self

    def without_collection(self):
        return self

    def run_in_background(self):
        try:
            result = self.op(None)
        except Exception as error:
            self.on_failure(error)
            return
        self.success(result)


class _HeldQueryOp(_QueryOp):
    """A QueryOp that waits in ``started`` until the test runs it."""

    started = []

    def run_in_background(self):
        self.started.append(self)

    def finish(self):
        _QueryOp.run_in_background(self)


def _pull(number, state="open", merged=False, merged_at=None, sha=None):
    return {
        "number": number,
        "title": f"Example pull request {number}",
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "head": {"ref": f"feature/{number}", "sha": sha or f"{number:040x}"},
        "user": {"login": "someone"},
    }


def _github(monkeypatch, responses):
    """Answer the updater's API calls from ``responses`` (endpoint -> payload).

    An endpoint that is missing answers 404, the way GitHub does.
    """
    calls = []

    def api_get(endpoint):
        calls.append(endpoint)
        updater._api_request_state.error = None
        if endpoint not in responses:
            updater._api_request_state.error = "not_found"
            return None
        return responses[endpoint]

    monkeypatch.setattr(updater, "_api_get", api_get)
    return calls


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(web, "QueryOp", _QueryOp)
    instance = web.UpdateManagerBridge(window=None)
    instance.sent = []
    instance._send = lambda callback, payload: instance.sent.append(
        (callback, payload)
    )
    return instance


def _lookup_replies(bridge):
    return [payload for callback, payload in bridge.sent if callback == "onPullRequestLookup"]


def test_lookup_adds_an_open_pull_request_the_install_path_accepts(bridge, monkeypatch):
    _github(monkeypatch, {
        "pulls?state=open&per_page=100": [_pull(2000)],
        "pulls/2000": _pull(2000),
    })
    # What the same pull request looks like when the open list loads it.
    listed_entry = updater.fetch_open_prs()[0]
    bridge._prs = [{"number": 1055, "title": "Loaded", "head_ref": "a", "head_sha": "b"}]

    assert bridge.lookupPullRequest("#2000") == {"pending": True}

    assert _lookup_replies(bridge) == [
        {"number": 2000, "status": "open", "pr": listed_entry},
    ]
    # Same keys and values as a listed pull request: nothing downstream can
    # tell a looked-up entry from a loaded one.
    assert [entry["number"] for entry in bridge._prs] == [1055, 2000]
    assert bridge._prs[1] == listed_entry
    source = bridge._resolve_source("pr", "2000")
    assert source["entry"] == listed_entry
    assert source["label"] == "PR #2000 (Example pull request 2000)"
    assert bridge._resolve_source("pr", "1055")["entry"]["number"] == 1055
    with pytest.raises(ValueError):
        bridge._resolve_source("pr", "9999")
    # A lookup is not an operation the install buttons wait for.
    assert not [callback for callback, _ in bridge.sent if callback == "onUpdateOperation"]


@pytest.mark.parametrize(
    ("pull", "status"),
    (
        (_pull(2002, state="closed"), "closed"),
        (_pull(2001, state="closed", merged=True, merged_at="2026-11-01T10:00:00Z"), "merged"),
        # The merged flag is not always in the payload; merged_at says it too.
        ({**_pull(2003, state="closed", merged_at="2026-11-01T10:00:00Z"), "merged": None}, "merged"),
    ),
)
def test_lookup_reports_a_closed_or_merged_pull_request(bridge, monkeypatch, pull, status):
    number = pull["number"]
    _github(monkeypatch, {f"pulls/{number}": pull})

    bridge.lookupPullRequest(str(number))

    assert _lookup_replies(bridge) == [{"number": number, "status": status}]
    assert bridge._prs == [] and bridge._looked_up_prs == {}
    with pytest.raises(ValueError):
        bridge._resolve_source("pr", str(number))


def _http_error(code, body, remaining=None):
    headers = Message()
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError(
        "https://api.github.com/repos/h0tp-ftw/ankimon/pulls/2000",
        code,
        "error",
        headers,
        io.BytesIO(body),
    )


@pytest.mark.parametrize(
    ("error", "status"),
    (
        (_http_error(404, b'{"message":"Not Found"}'), "not_found"),
        (_http_error(403, b'{"message":"API rate limit exceeded"}', remaining="0"), "rate_limited"),
        (_http_error(429, b"{}"), "rate_limited"),
        (urllib.error.URLError("offline"), "connection"),
        (_http_error(502, b"{}"), "api_error"),
    ),
)
def test_lookup_maps_github_errors_like_the_rest_of_the_updater(
    bridge, monkeypatch, error, status
):
    # Through the real _api_get, so the mapping is the one every other
    # request uses.
    def urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)

    bridge.lookupPullRequest("2000")

    assert _lookup_replies(bridge) == [{"number": 2000, "status": status}]
    assert bridge._prs == []
    # The worker consumed the error itself (it is per thread); none is left.
    assert updater.consume_api_error() is None


@pytest.mark.parametrize(
    "text",
    ("", " ", "0", "#0", "00", "abc", "-5", "12a", "1e3", "##12", "1 2", "1234567890123"),
)
def test_lookup_refuses_anything_but_a_positive_pull_request_number(
    bridge, monkeypatch, text
):
    calls = _github(monkeypatch, {})

    assert bridge.lookupPullRequest(text) == {
        "success": False,
        "message": "Enter a pull request number.",
    }
    assert calls == []
    assert _lookup_replies(bridge) == [{"number": text, "status": "invalid"}]
    assert bridge._prs == []


def test_lookup_keeps_one_entry_per_number(bridge, monkeypatch):
    responses = {"pulls/2000": _pull(2000), "pulls/1055": _pull(1055, sha="f" * 40)}
    _github(monkeypatch, responses)
    bridge._prs = [{"number": 1055, "title": "Loaded", "head_ref": "a", "head_sha": "old"}]

    bridge.lookupPullRequest("2000")
    bridge.lookupPullRequest("02000")
    bridge.lookupPullRequest("1055")

    assert sorted(entry["number"] for entry in bridge._prs) == [1055, 2000]
    # Looking up a listed number refreshes its entry rather than adding one.
    assert bridge._resolve_source("pr", "1055")["entry"]["head_sha"] == "f" * 40
    assert sorted(bridge._looked_up_prs) == ["1055", "2000"]


def test_developer_reload_keeps_pull_requests_found_by_number(bridge, monkeypatch):
    responses = {
        "tags?per_page=100": [],
        "releases?per_page=100": [],
        "branches": [],
        "pulls?state=open&per_page=100": [_pull(1055)],
        "pulls/2000": _pull(2000, sha="1" * 40),
    }
    _github(monkeypatch, responses)
    bridge.lookupPullRequest("2000")

    bridge.loadDeveloperData()

    # The reload lists 1055 only, and 2000 (found by number, perhaps still
    # selected in the page) stays installable.
    assert sorted(entry["number"] for entry in bridge._prs) == [1055, 2000]
    assert bridge._resolve_source("pr", "2000")["entry"]["head_sha"] == "1" * 40
    developer = [payload for callback, payload in bridge.sent if callback == "onDeveloperData"]
    assert [entry["number"] for entry in developer[-1]["prs"]] == [1055]

    # Once the loaded list has it too, its (fresher) entry is the only one.
    responses["pulls?state=open&per_page=100"] = [_pull(1055), _pull(2000, sha="2" * 40)]
    bridge.loadDeveloperData()

    assert sorted(entry["number"] for entry in bridge._prs) == [1055, 2000]
    assert bridge._resolve_source("pr", "2000")["entry"]["head_sha"] == "2" * 40


def test_lookup_runs_off_the_ui_thread_once_per_number(bridge, monkeypatch):
    calls = _github(monkeypatch, {"pulls/2000": _pull(2000), "pulls/2001": _pull(2001)})
    monkeypatch.setattr(web, "QueryOp", _HeldQueryOp)
    monkeypatch.setattr(_HeldQueryOp, "started", [])

    assert bridge.lookupPullRequest("2000") == {"pending": True}
    assert bridge.lookupPullRequest("2000") == {"pending": True}
    assert bridge.lookupPullRequest("2001") == {"pending": True}

    # The slot returned before GitHub was asked anything; the same number
    # asked twice is one request, a second number is its own.
    assert calls == []
    assert len(_HeldQueryOp.started) == 2
    for operation in _HeldQueryOp.started:
        operation.finish()
    assert calls == ["pulls/2000", "pulls/2001"]
    assert [reply["number"] for reply in _lookup_replies(bridge)] == [2000, 2001]

    # Finished lookups can run again.
    bridge.lookupPullRequest("2000")
    assert len(_HeldQueryOp.started) == 3


def test_a_lookup_that_breaks_still_answers_the_page(bridge, monkeypatch):
    def broken(_number):
        raise RuntimeError("boom")

    monkeypatch.setattr(updater, "fetch_pull_request", broken)

    bridge.lookupPullRequest("2000")

    # The code error is reported, and the "Looking up" line still resolves.
    assert [callback for callback, _ in bridge.sent] == [
        "onUpdateCodeError",
        "onPullRequestLookup",
    ]
    assert _lookup_replies(bridge) == [{"number": 2000, "status": "api_error"}]
