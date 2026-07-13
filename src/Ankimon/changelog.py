import time
from typing import Union

from aqt import gui_hooks, mw
from aqt.operations import QueryOp
from aqt.utils import showWarning

from .resources import addon_ver, addon_dir
from .utils import read_github_file, read_local_file, compare_files, write_local_file
from .pyobj.error_handler import show_warning_with_traceback
from .services import services

update_infos_md = addon_dir / "updateinfos.md"


def _log_info(message: str) -> None:
    """Log through the services registry when a logger is wired (production);
    stay silent headless / in tests where ``services.logger`` is None."""
    logger = services.logger
    if logger is not None:
        logger.log("info", message)


def download_changelog():
    try:
        github_url = f"https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/changelogs/{addon_ver}.md"
        github_content = read_github_file(github_url)
        if github_content is None:
            github_url = "https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/changelogs/unknown.md"
            github_content = read_github_file(github_url)
        return github_content
    except Exception as e:
        return e


def check_and_show_changelog(online_connectivity: bool, ssh: bool, no_more_news: bool):
    if not (online_connectivity and ssh):
        return

    def done(result: Union[Exception, str, None]):
        if isinstance(result, Exception):
            show_warning_with_traceback(
                parent=mw, exception=result, message="Error connecting to GitHub:"
            )
            return
        if result is None:
            showWarning("Failed to retrieve Ankimon content from GitHub.")
            return
        local_content = read_local_file(update_infos_md)
        if not compare_files(local_content, result):
            write_local_file(update_infos_md, result)
            # Lazy imports: markdown ships with aqt and the notification window
            # is Qt — neither is available (nor needed) in the headless tier.
            import markdown

            from .gui_entities import UpdateNotificationWindow

            dialog = UpdateNotificationWindow(markdown.markdown(result))
            if not no_more_news:
                dialog.exec()

    QueryOp(
        parent=mw,
        op=lambda _col: download_changelog(),
        success=done,
    ).without_collection().run_in_background()


def _is_snoozed(state: dict) -> bool:
    """True while a weekly ``skip_until`` snooze (set from either the branch or
    release update prompt) is still active. ``skip_until`` lives in the
    user-editable ``update_state.json``, so a null/non-numeric value must not
    crash the comparison — it's simply treated as "not snoozed"."""
    skip_until = state.get("skip_until")
    return isinstance(skip_until, (int, float)) and time.time() < skip_until


def open_help_window(online_connectivity):
    try:
        from .pyobj.help_window import HelpWindow

        help_dialog = HelpWindow(online_connectivity)
        help_dialog.exec()
    except Exception as e:
        show_warning_with_traceback(
            parent=mw, exception=e, message="Error in opening Help Guide:"
        )


def check_branch_update(online_connectivity: bool, ssh: bool):
    """Poll GitHub for new commits on the branch this install came from.

    Only acts when ``update_state.json`` records a branch install (defaulting
    to main on fresh installs); honors the weekly ``skip_until`` snooze. On a
    new remote SHA it shows the update prompt with the pending commit feed.
    """
    _log_info(
        f"check_branch_update triggered: online_connectivity={online_connectivity}, ssh={ssh}"
    )
    if not ssh:
        _log_info("check_branch_update exited early: ssh is False")
        return

    from .pyobj.update_manager import read_update_state
    from .resources import addon_ver

    state = read_update_state()
    _log_info(f"check_branch_update: read_update_state={state}")
    if not state or state.get("addon_version") != addon_ver:
        from .pyobj.update_manager import is_git_clone
        if is_git_clone():
            _log_info("check_branch_update exited early: git clone detected")
            return

        # Only branch installs poll a branch SHA. A pre-existing release/tag/PR
        # install has a source_name that is NOT a branch, so fetch_branch_sha
        # would 404 and never persist addon_version — repeating the wasted GitHub
        # round-trip every startup (and risking a release->branch source_type
        # coercion if a tag happened to share a branch name). Fresh installs
        # (no state) still fall through and default to the main branch.
        if state and state.get("source_type") != "branch":
            _log_info("check_branch_update: skipping branch init for non-branch install")
            return

        branch_name = (state.get("source_name") if state else None) or "main"

        # Initialize branch check tracking silently
        def bg_init(_col):
            try:
                from .pyobj.update_manager import fetch_branch_sha, save_update_state
                remote_sha = fetch_branch_sha(branch_name)
                if remote_sha:
                    save_update_state("branch", branch_name, remote_sha)
                return remote_sha
            except Exception as e:
                return e

        QueryOp(
            parent=mw,
            op=bg_init,
            success=lambda res: _log_info(f"Initialized update_state.json for {branch_name} branch: {res}"),
        ).without_collection().run_in_background()
        return

    _log_info(
        f"check_branch_update: skip_until={state.get('skip_until')}, current_time={time.time()}"
    )
    # update_state.json is user-editable: a null/non-numeric skip_until must not
    # crash the comparison (keep in sync with UpdateDialog._populate_brrr_ui).
    if _is_snoozed(state):
        _log_info("check_branch_update exited early: skip_until active")
        return

    source_type = state.get("source_type")
    source_name = state.get("source_name") or "main"
    local_sha = state.get("commit_sha")
    _log_info(
        f"check_branch_update: source_type={source_type}, source_name={source_name}, local_sha={local_sha}"
    )

    if source_type != "branch":
        _log_info("check_branch_update exited early: source_type is not branch")
        return

    def bg(_col):
        try:
            from .pyobj.update_manager import fetch_branch_sha, fetch_branch_commits

            remote_sha = fetch_branch_sha(source_name)
            commits = []
            if remote_sha and local_sha != remote_sha:
                commits = fetch_branch_commits(source_name, local_sha)
            return remote_sha, commits
        except Exception as e:
            return e

    def done(result):
        if isinstance(result, Exception) or not result:
            return

        remote_sha, commits = result
        if not remote_sha:
            return

        if local_sha != remote_sha:
            from .pyobj.update_dialog import show_branch_update_prompt

            show_branch_update_prompt(source_name, remote_sha, commits)

    QueryOp(
        parent=mw,
        op=bg,
        success=done,
    ).without_collection().run_in_background()


def check_for_update(online_connectivity: bool, ssh: bool):
    """Channel-aware update poll — the profile-open entry point.

    Routes to the right check for the user's selected channel (see update_manager):
    ``main`` reuses the branch-SHA commit poll (:func:`check_branch_update`);
    ``stable`` / ``experimental`` poll the newest release tag *without* / *with*
    the ``-E`` suffix. Kept as a thin dispatcher so the branch poll — and its
    tests — stay exactly as they were.
    """
    if not ssh:
        return

    from .pyobj.update_manager import get_update_channel, CHANNEL_MAIN

    channel = get_update_channel()
    _log_info(f"check_for_update: channel={channel}")
    if channel == CHANNEL_MAIN:
        check_branch_update(online_connectivity, ssh)
    else:
        _poll_release_channel(channel)


def _poll_release_channel(channel: str):
    """Prompt when the newest release on a release channel (stable/experimental)
    is strictly newer than the installed version.

    The installed ``addon_ver`` is the baseline — no per-channel state is needed,
    so once the user updates the same release stops prompting. Dev clones (which
    update via ``git pull``) and the weekly ``skip_until`` snooze are honored.
    """
    from .pyobj.update_manager import (
        is_git_clone,
        latest_release_for_channel,
        is_newer_version,
        read_update_state,
    )

    if is_git_clone():
        _log_info("_poll_release_channel exited early: git clone (use git pull)")
        return

    state = read_update_state() or {}
    if _is_snoozed(state):
        _log_info("_poll_release_channel exited early: skip_until active")
        return

    def bg(_col):
        try:
            return latest_release_for_channel(channel)
        except Exception as e:
            return e

    def done(result):
        if isinstance(result, Exception) or not result:
            return
        release = result
        tag = release.get("name")
        if tag and is_newer_version(tag, addon_ver):
            from .pyobj.update_dialog import show_release_update_prompt

            show_release_update_prompt(channel, release)

    QueryOp(parent=mw, op=bg, success=done).without_collection().run_in_background()


def schedule_branch_update_check(online_connectivity: bool, ssh: bool) -> None:
    """Schedule the branch-update poll for after the profile opens.

    Boot scheduling goes through the gui_hooks seam (profile-open path)
    rather than running as a module-level side effect at addon import time.
    The connectivity gate mirrors the upstream call site, which only polled
    when a connection was available.
    """

    def _on_profile_open() -> None:
        if online_connectivity:
            check_for_update(online_connectivity, ssh)
            try:
                from .pyobj.sprite_updater import trigger_sprites_update_check
                trigger_sprites_update_check(parent=mw, silent=True)
            except Exception as e:
                _log_info(f"Failed to start silent sprite update check: {e}")

    gui_hooks.profile_did_open.append(_on_profile_open)
