from typing import Union

import markdown
from aqt import mw
from aqt.operations import QueryOp
from aqt.utils import showWarning

from .gui_entities import UpdateNotificationWindow
from .pyobj.error_handler import show_warning_with_traceback
from .pyobj.help_window import HelpWindow
from .resources import addon_dir, addon_ver
from .utils import compare_files, read_github_file, read_local_file, write_local_file

update_infos_md = addon_dir / "updateinfos.md"


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
            dialog = UpdateNotificationWindow(markdown.markdown(result))
            if not no_more_news:
                dialog.exec()

    QueryOp(
        parent=mw,
        op=lambda _col: download_changelog(),
        success=done,
    ).without_collection().run_in_background()


def open_help_window(online_connectivity):
    try:
        help_dialog = HelpWindow(online_connectivity)
        help_dialog.exec()
    except Exception as e:
        show_warning_with_traceback(
            parent=mw, exception=e, message="Error in opening Help Guide:"
        )
