import json
import time
import traceback
from pathlib import Path
from urllib.parse import quote

from aqt import mw
from aqt.operations import QueryOp
from PyQt6.QtCore import QObject, QUrl, Qt, pyqtSlot
from PyQt6.QtGui import QDesktopServices

from ..resources import IS_EXPERIMENTAL_BUILD, addon_ver, user_path_sprites
from ..services import services
from . import update_manager as updater


_sprite_check_up_to_date = False


class UpdateManagerBridge(QObject):
    """Web UI adapter for the existing updater operations."""

    def __init__(self, window, logger=None):
        super().__init__()
        self._window = window
        self._logger = logger
        self._active_operations = set()
        self._releases = []
        self._tags = []
        self._branches = []
        self._prs = []
        self._installed_pr = None
        self._active_branch = "main"
        self._git_clone = False
        self._sprite_diff = None
        self._sprite_thread = None
        self._close_after_sprite_update = False
        self._sprite_check_up_to_date = _sprite_check_up_to_date

    def _log_error(self, message):
        if self._logger:
            self._logger.log("error", message)

    def _send(self, callback, payload):
        script = (
            f"if (typeof window[{json.dumps(callback)}] === 'function') "
            f"window[{json.dumps(callback)}]({json.dumps(payload)});"
        )
        try:
            self._window.webview_update.page().runJavaScript(script)
        except Exception as error:
            self._log_error(
                f"Could not deliver Update Manager event {callback}: {error}"
            )

    def _send_from_worker(self, callback, payload):
        try:
            if mw is not None and getattr(mw, "taskman", None):
                mw.taskman.run_on_main(lambda: self._send(callback, payload))
            else:
                self._send(callback, payload)
        except Exception as error:
            self._log_error(
                f"Could not queue Update Manager event {callback}: {error}"
            )

    def _report_code_error(self, error):
        details = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self._log_error(f"Update Manager code error: {error}\n{details}")
        self._send(
            "onUpdateCodeError",
            {"message": str(error), "traceback": details},
        )

    @pyqtSlot(result=bool)
    def openGitHub(self):
        opened = QDesktopServices.openUrl(QUrl("https://github.com/h0tp-ftw/ankimon"))
        if not opened:
            self._log_error("Could not open Ankimon's GitHub page in the default browser.")
        return opened

    @pyqtSlot(str, result=bool)
    def openGitHubTarget(self, target):
        url = QUrl(target)
        path = url.path()
        if (
            not url.isValid()
            or url.scheme().lower() != "https"
            or url.host().lower() != "github.com"
            or url.userName()
            or url.password()
            or url.port(-1) not in (-1, 443)
            or not (
                path == "/h0tp-ftw/ankimon"
                or path.startswith("/h0tp-ftw/ankimon/")
            )
        ):
            self._log_error(f"Rejected invalid Update Manager GitHub URL: {target}")
            return False
        opened = QDesktopServices.openUrl(url)
        if not opened:
            self._log_error(f"Could not open GitHub source URL: {target}")
        return opened

    def _start_query(self, operation, worker, success, failure=None):
        if operation in self._active_operations:
            return False
        self._active_operations.add(operation)
        self._send("onUpdateOperation", {"kind": operation, "busy": True})

        def finish():
            self._active_operations.discard(operation)
            self._send("onUpdateOperation", {"kind": operation, "busy": False})

        def on_success(result):
            finish()
            try:
                success(result)
            except Exception as error:
                self._report_code_error(error)
                if failure:
                    failure(error)

        def on_failure(error):
            finish()
            self._report_code_error(error)
            if failure:
                failure(error)

        try:
            QueryOp(parent=self._window, op=worker, success=on_success).failure(
                on_failure
            ).without_collection().run_in_background()
        except Exception as error:
            finish()
            self._report_code_error(error)
            if failure:
                failure(error)
        return True

    @pyqtSlot(result="QVariant")
    def loadData(self):
        def worker(_col):
            try:
                releases = updater.fetch_releases()
            except Exception:
                releases = []
                releases_error = "api_error"
            else:
                releases_error = updater.consume_api_error()

            git_clone = updater.is_git_clone()
            git_info = updater.get_git_checkout_info() if git_clone else {}
            state = updater.read_update_state() or {}
            source_type = state.get("source_type") or "branch"
            source_name = str(state.get("source_name") or "main")
            if git_clone:
                local_sha = git_info.get("full_sha") or None
                source_type = "branch"
                source_name = git_info.get("branch") or "main"
                if source_name == "HEAD":
                    source_name = "main"
                state = dict(state, commit_sha=local_sha)
            else:
                local_sha = state.get("commit_sha")

            try:
                main_remote_sha = updater.fetch_branch_sha("main")
            except Exception:
                main_remote_sha = None
                main_error = "api_error"
            else:
                main_error = updater.consume_api_error()
                if main_remote_sha is None and main_error is None:
                    main_error = "not_found"

            is_main_source = source_type == "branch" and source_name == "main"
            if (git_clone and source_name == "main") or is_main_source:
                main_commit_sha = local_sha
            else:
                main_commit_sha = state.get("main_commit_sha")
            if not isinstance(main_commit_sha, str):
                main_commit_sha = None
            installed_pr = None
            current_error = None
            if is_main_source:
                current_remote_sha = main_remote_sha
                current_error = main_error
            elif source_type == "pr":
                try:
                    installed_pr = updater.fetch_pull_request(source_name)
                except Exception:
                    installed_pr = None
                    current_remote_sha = None
                    current_error = "api_error"
                else:
                    current_error = updater.consume_api_error()
                    current_remote_sha = (
                        installed_pr.get("head_sha") if installed_pr else None
                    )
                    if current_remote_sha is None and current_error is None:
                        current_error = "not_found"
            else:
                try:
                    current_remote_sha = updater.fetch_source_sha(
                        source_type, source_name
                    )
                except Exception:
                    current_remote_sha = None
                    current_error = "api_error"
                else:
                    current_error = updater.consume_api_error()
                    if current_remote_sha is None and current_error is None:
                        current_error = "not_found"
            if (
                source_type in {"tag", "release"}
                and local_sha == source_name
                and current_remote_sha
            ):
                local_sha = current_remote_sha

            relation = None
            if git_clone and local_sha and current_remote_sha and current_remote_sha != local_sha:
                try:
                    relation = updater.fetch_branch_relation(local_sha, source_name)
                except Exception:
                    relation = None
                state["git_relation"] = relation

            commit_date = state.get("commit_date")
            if not commit_date:
                try:
                    commit_date = (
                        updater.fetch_commit_date(local_sha) if local_sha else None
                    )
                except Exception:
                    commit_date = None
            main_commit_date = state.get("main_commit_date")
            if not main_commit_date:
                try:
                    main_commit_date = (
                        updater.fetch_commit_date(main_commit_sha)
                        if main_commit_sha else None
                    )
                except Exception:
                    main_commit_date = None
            if not git_clone:
                updater.persist_update_state_commit_metadata(
                    commit_sha=local_sha,
                    commit_date=commit_date,
                    main_commit_sha=main_commit_sha,
                    main_commit_date=main_commit_date,
                )

            commit_cache = {}

            def commits_between(base_sha, head_sha):
                key = (base_sha, head_sha)
                if key not in commit_cache:
                    commit_cache[key] = (
                        updater.fetch_commits_between(*key),
                        updater.consume_api_error(),
                    )
                return commit_cache[key]

            main_commits_error = None
            try:
                if (
                    main_commit_sha
                    and main_remote_sha
                    and main_commit_sha != main_remote_sha
                ):
                    main_commits, main_commits_error = commits_between(
                        main_commit_sha, main_remote_sha
                    )
                elif main_remote_sha and not main_commit_sha:
                    main_commits = updater.fetch_branch_commits("main")
                    main_commits_error = updater.consume_api_error()
                else:
                    main_commits = []
            except Exception:
                main_commits = []
                main_commits_error = "api_error"
            try:
                if local_sha and current_remote_sha and local_sha != current_remote_sha:
                    current_commits, _ = commits_between(local_sha, current_remote_sha)
                else:
                    current_commits = []
            except Exception:
                current_commits = []

            latest_release = releases[0] if releases else None
            latest_release_sha = None
            release_lost_commits = []
            bleeding_edge_release = False
            if latest_release and is_main_source and main_commit_sha:
                try:
                    latest_release_sha = updater.fetch_source_sha(
                        "release", latest_release["name"]
                    )
                except Exception:
                    latest_release_sha = None
                if latest_release_sha and latest_release_sha != main_commit_sha:
                    bleeding_edge_release = True
                    try:
                        release_lost_commits, _ = commits_between(
                            latest_release_sha, main_commit_sha
                        )
                    except Exception:
                        release_lost_commits = []

            installed_at = state.get("installed_at")
            if not isinstance(installed_at, (int, float)) or not installed_at:
                try:
                    path = updater.get_update_state_path()
                    installed_at = path.stat().st_mtime if path.exists() else None
                except OSError:
                    installed_at = None

            sprite_state_path = (
                Path(user_path_sprites).parent / "sprites_update_state.json"
            )
            sprite_snoozed = False
            if sprite_state_path.exists():
                try:
                    sprite_state = json.loads(
                        sprite_state_path.read_text(encoding="utf-8")
                    )
                    snooze_until = sprite_state.get("snooze_until")
                    sprite_snoozed = (
                        isinstance(snooze_until, (int, float))
                        and time.time() < snooze_until
                    )
                except Exception:
                    pass

            active_branch = (
                "detached"
                if git_clone and git_info.get("branch") == "HEAD"
                else source_name
            )
            if source_type == "pr":
                active_branch = (
                    f"PR #{source_name} — {installed_pr.get('head_ref')}"
                    if installed_pr and installed_pr.get("head_ref")
                    else f"PR #{source_name}"
                )
            elif source_type in {"tag", "release"}:
                active_branch = f"{source_type.capitalize()}: {source_name}"
            show_branch_views = bool(
                not git_clone and not is_main_source and state.get("source_type")
            )
            return {
                "releases": releases,
                "releases_error": releases_error,
                "state": state,
                "git_clone": git_clone,
                "git_info": git_info,
                "active_branch": active_branch,
                "query_branch": source_name,
                "local_sha": local_sha,
                "remote_sha": current_remote_sha,
                "branch_error": current_error,
                "main_remote_sha": main_remote_sha,
                "main_commit_sha": main_commit_sha,
                "main_error": main_error,
                "main_commits": main_commits,
                "main_commits_error": main_commits_error,
                "current_commits": current_commits,
                "bleeding_edge_release": bleeding_edge_release,
                "release_lost_commits": release_lost_commits,
                "show_branch_views": show_branch_views,
                "installed_pr": installed_pr,
                "source_type": source_type,
                "source_name": source_name,
                "relation": relation,
                "commit_date": commit_date,
                "main_commit_date": main_commit_date,
                "installed_at": installed_at,
                "sprite_snoozed": sprite_snoozed,
            }

        def done(data):
            self._releases = data["releases"]
            self._git_clone = data["git_clone"]
            self._active_branch = data["active_branch"]
            self._installed_pr = data.get("installed_pr")
            self._send(
                "onUpdateData",
                {
                    **data,
                    "version": addon_ver,
                    "experimental_build": IS_EXPERIMENTAL_BUILD,
                    "channel": updater.get_update_channel(),
                    "skip_until": (data["state"] or {}).get("skip_until"),
                    "sprite_check_up_to_date": self._sprite_check_up_to_date,
                },
            )

        def failed(error):
            self._send("onUpdateLoadFailed", {"message": str(error)})

        self._start_query("load", worker, done, failed)
        return {"pending": True}

    @pyqtSlot(result="QVariant")
    def loadDeveloperData(self):
        release_order = list(self._releases)

        def worker(_col):
            tags = updater.fetch_tags()
            tag_error = updater.consume_api_error()
            ordered_releases = release_order
            if not ordered_releases:
                try:
                    ordered_releases = updater.fetch_releases()
                    updater.consume_api_error()
                except Exception as error:
                    self._log_error(
                        f"Could not load release order for Update Manager tags: {error}"
                    )
            branches = updater.fetch_branches()
            branch_error = updater.consume_api_error()
            prs = updater.fetch_open_prs()
            pr_error = updater.consume_api_error()
            return {
                "tags": updater.order_tags_by_release_order(
                    tags, ordered_releases
                ),
                "branches": branches,
                "prs": prs,
                "errors": {
                    "tag": tag_error,
                    "branch": branch_error,
                    "pr": pr_error,
                },
            }

        def done(data):
            self._tags = data["tags"]
            self._branches = data["branches"]
            self._prs = data["prs"]
            self._send("onDeveloperData", data)

        def failed(error):
            self._send("onDeveloperLoadFailed", {"message": str(error)})

        self._start_query("developer", worker, done, failed)
        return {"pending": True}

    @pyqtSlot(result=bool)
    def getDeveloperWarningHidden(self):
        database = services.db
        if database is None:
            self._log_error("Could not read the developer warning preference: Ankimon database is unavailable.")
            return False
        try:
            value = database.get_user_data("hide_developer_warning")
            if value is None:
                database.set_user_data("hide_developer_warning", 0)
                return False
            return value in (1, True, "1", "true")
        except Exception as error:
            self._report_code_error(error)
            return False

    @pyqtSlot(bool, result=bool)
    def setDeveloperWarningHidden(self, hidden):
        database = services.db
        if database is None:
            self._log_error("Could not save the developer warning preference: Ankimon database is unavailable.")
            return False
        try:
            database.set_user_data("hide_developer_warning", 1 if hidden else 0)
        except Exception as error:
            self._report_code_error(error)
            return False
        return True

    @pyqtSlot(str, result=bool)
    def setChannel(self, channel):
        if channel not in updater.UPDATE_CHANNELS:
            return False
        try:
            updater.set_update_channel(channel)
        except Exception as error:
            self._report_code_error(error)
            return False
        return True

    @pyqtSlot(bool, result=bool)
    def setBranchSnooze(self, snoozed):
        try:
            updater.set_update_skip_until(time.time() + 604800 if snoozed else 0)
        except Exception as error:
            self._report_code_error(error)
            return False
        return True

    @pyqtSlot(bool, result=bool)
    def setSpriteSnooze(self, snoozed):
        state_path = Path(user_path_sprites).parent / "sprites_update_state.json"
        state_data = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state_data = {}
        state_data["snooze_until"] = (
            time.time() + 7 * 24 * 60 * 60 if snoozed else 0
        )
        try:
            state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        except Exception as error:
            self._report_code_error(error)
            return False
        return True

    def _resolve_source(self, source_type, source_name):
        source_name = str(source_name or "")
        if source_type == "installed":
            state = updater.read_update_state() or {}
            installed_type = state.get("source_type")
            installed_name = str(state.get("source_name") or "")
            if installed_type == "branch" and installed_name and installed_name != "main":
                return {
                    "source_type": "branch",
                    "source_name": installed_name,
                    "label": f"latest {installed_name}",
                }
            if installed_type == "pr" and installed_name.isdigit():
                entry = self._installed_pr or next(
                    (
                        item for item in self._prs
                        if str(item.get("number")) == installed_name
                    ),
                    None,
                )
                if entry:
                    return {
                        "source_type": "pr",
                        "source_name": installed_name,
                        "label": f"PR #{installed_name} ({entry.get('title', '')})",
                        "entry": entry,
                    }
            if installed_type in {"tag", "release"} and installed_name:
                choices = self._releases if installed_type == "release" else self._tags
                entry = next(
                    (item for item in choices if item.get("name") == installed_name),
                    None,
                ) or {
                    "name": installed_name,
                    "zipball_url": (
                        f"https://github.com/h0tp-ftw/ankimon/archive/"
                        f"refs/tags/{quote(installed_name, safe='')}.zip"
                    ),
                }
                return {
                    "source_type": installed_type,
                    "source_name": installed_name,
                    "label": f"latest {installed_type} {installed_name}",
                    "entry": entry,
                }
            raise ValueError("The installed source is not available to update.")
        if source_type == "current" and self._git_clone:
            return {
                "source_type": "current",
                "source_name": "current",
                "label": "current Git branch",
            }
        if source_type == "branch":
            allowed = {entry.get("name") for entry in self._branches}
            if source_name not in allowed and source_name not in {
                "main",
                self._active_branch,
            }:
                raise ValueError("Select a branch from the loaded list.")
            return {
                "source_type": "branch",
                "source_name": source_name,
                "label": (
                    f"latest {source_name}"
                    if source_name == self._active_branch
                    else f"branch {source_name}"
                ),
            }
        if source_type in ("release", "tag"):
            choices = self._releases if source_type == "release" else self._tags
            entry = next(
                (item for item in choices if item.get("name") == source_name),
                None,
            )
            if entry:
                return {
                    "source_type": source_type,
                    "source_name": source_name,
                    "label": f"{source_type} {source_name}",
                    "entry": entry,
                }
        if source_type == "pr":
            entry = next(
                (item for item in self._prs if str(item.get("number")) == source_name),
                None,
            )
            if entry:
                return {
                    "source_type": "pr",
                    "source_name": source_name,
                    "label": f"PR #{entry['number']} ({entry['title']})",
                    "entry": entry,
                }
        raise ValueError("Select a valid update source.")

    @pyqtSlot(str, str, result="QVariant")
    def installUpdate(self, source_type, source_name):
        try:
            source = self._resolve_source(source_type, source_name)
        except ValueError as error:
            return {"success": False, "message": str(error)}
        except Exception as error:
            self._report_code_error(error)
            return {"success": False, "message": str(error)}

        warning = (
            "WARNING: Anyone can open a pull request. This code is unreviewed and "
            "unreleased — installing it runs unverified code on your computer. "
            "Only proceed if you trust this PR. Use at your own risk."
            if source["source_type"] == "pr"
            else ""
        )

        def worker(_col):
            source_type = source["source_type"]
            source_name = source["source_name"]
            commit_sha = None
            published_at = None
            previous_state = updater.read_update_state() or {}
            main_commit_sha = previous_state.get("main_commit_sha")
            main_commit_date = previous_state.get("main_commit_date")
            if source_type == "pr":
                entry = source["entry"]
                commit_sha = entry["head_sha"]
                download = lambda progress: updater._download_pr_zip(
                    entry["head_sha"], progress
                )
            elif source_type in ("release", "tag"):
                entry = source["entry"]
                commit_sha = updater.fetch_source_sha(source_type, source_name)
                if not commit_sha:
                    commit_sha = source_name
                published_at = (
                    entry.get("published_at")
                    if source_type == "release"
                    else updater.published_at_for_tag(source_name, self._releases)
                )
                download = lambda progress: updater._download_zip_to_temp(
                    entry["zipball_url"], progress
                )
            elif source_type in ("branch", "current"):
                if source_type == "branch":
                    commit_sha = updater.fetch_branch_sha(source_name)
                download = lambda progress: updater._download_branch_zip(
                    self._active_branch if source_type == "current" else source_name,
                    progress,
                )
            else:
                raise ValueError("Unsupported update source.")

            if source_type == "branch" and source_name == "main":
                main_commit_sha = commit_sha
                main_commit_date = (
                    updater.fetch_commit_date(commit_sha) if commit_sha else None
                )
            elif not main_commit_sha:
                if (
                    previous_state.get("source_type") == "branch"
                    and previous_state.get("source_name") == "main"
                ):
                    main_commit_sha = previous_state.get("commit_sha")
                    main_commit_date = (
                        main_commit_date or previous_state.get("commit_date")
                    )
                else:
                    try:
                        main_commit_sha = updater.fetch_branch_sha("main")
                    except Exception:
                        main_commit_sha = None
                    if main_commit_sha:
                        try:
                            main_commit_date = updater.fetch_commit_date(
                                main_commit_sha
                            )
                        except Exception:
                            main_commit_date = None

            if self._git_clone:
                messages = []

                def status(message):
                    messages.append(message)
                    self._send_from_worker("onUpdateStatus", {"message": message})
                    if message.startswith("Fetching "):
                        percent = 12
                    elif message.startswith(("Fast-forwarding ", "Checking out ")):
                        percent = 60
                    elif message == "Updating submodules...":
                        percent = 85
                    else:
                        return
                    self._send_from_worker("onUpdateProgress", {"percent": percent})

                success, message = updater.git_checkout_source(
                    source_type,
                    source_name,
                    status_cb=status,
                )
                return success, message, messages, None

            def progress(current, total):
                if total > 0:
                    self._send_from_worker(
                        "onUpdateProgress",
                        {"percent": int(current / total * 40)},
                    )

            messages = []

            def status(message):
                messages.append(message)
                if message.startswith("__PROGRESS__"):
                    try:
                        current, total = message[len("__PROGRESS__") :].split("|", 1)
                        total = int(total)
                        percent = int(int(current) / total * 100) if total > 0 else 0
                        self._send_from_worker(
                            "onUpdateProgress",
                            {"percent": 40 + int(min(100, max(0, percent)) * 0.6)},
                        )
                    except (TypeError, ValueError):
                        pass
                else:
                    self._send_from_worker("onUpdateStatus", {"message": message})

            zip_path = download(progress)
            if not zip_path:
                return (
                    False,
                    "Download failed. Check your internet connection.",
                    [],
                    None,
                )

            success, message, pending_mod = updater.apply_update(
                zip_path,
                source_type,
                source_name,
                commit_sha,
                published_at,
                status_cb=status,
                main_commit_sha=main_commit_sha,
                main_commit_date=main_commit_date,
            )
            return success, message, messages, pending_mod

        def done(result):
            success, message, messages, pending_mod = result
            if success and pending_mod:
                updater.stamp_addon_mod(pending_mod)
            self._send(
                "onUpdateFinished",
                {
                    "success": success,
                    "message": message,
                    "status": messages[-1] if messages else message,
                    "git": self._git_clone,
                },
            )
            if success:
                self.loadData()

        def failed(error):
            self._send(
                "onUpdateFinished",
                {"success": False, "unexpected": True, "message": str(error)},
            )

        if "install" in self._active_operations:
            return {
                "success": False,
                "message": "An update is already in progress.",
            }
        self._send(
            "onUpdateInstallStarted",
            {"label": source["label"], "warning": warning},
        )
        if not self._start_query("install", worker, done, failed):
            return {
                "success": False,
                "message": "An update is already in progress.",
            }
        return {"pending": True}

    @pyqtSlot()
    def checkSprites(self):
        try:
            from .sprite_updater import calculate_sprite_diff
        except Exception as error:
            self._report_code_error(error)
            return

        dest_dir = Path(user_path_sprites)

        def worker(_col):
            return calculate_sprite_diff(
                dest_dir,
                silent=False,
                ignore_snooze=True,
                progress_callback=lambda percent, message: self._send_from_worker(
                    "onSpritesCheckProgress",
                    {"percent": percent, "message": message},
                ),
            )

        def done(result):
            global _sprite_check_up_to_date
            self._sprite_diff = (
                result if result.get("status") == "update_available" else None
            )
            self._sprite_check_up_to_date = (
                result.get("status") == "up_to_date"
            )
            _sprite_check_up_to_date = self._sprite_check_up_to_date
            self._send("onSpritesChecked", result)

        def failed(error):
            global _sprite_check_up_to_date
            self._sprite_check_up_to_date = False
            _sprite_check_up_to_date = False
            self._send("onSpritesCheckFailed", {"message": str(error)})

        self._start_query("sprites-check", worker, done, failed)

    @pyqtSlot(result="QVariant")
    def installSprites(self):
        if self._sprite_thread is not None and self._sprite_thread.isRunning():
            return {"success": False, "message": "A sprite update is already running."}
        if not self._sprite_diff:
            return {"success": False, "message": "Check for sprite updates first."}

        try:
            from .sprite_updater import SpriteUpdateDiffThread

            data = self._sprite_diff
            completion = {}
            thread = SpriteUpdateDiffThread(
                data.get("added", []),
                data.get("modified", []),
                data.get("deleted", []),
                data.get("remote_sha"),
                Path(user_path_sprites),
            )
            self._sprite_thread = thread
            self._send(
                "onUpdateOperation",
                {"kind": "sprites-install", "busy": True},
            )
            thread.progress_signal.connect(
                lambda value: self._send_from_worker(
                    "onSpriteProgress", {"percent": value}
                )
            )
            thread.status_signal.connect(
                lambda message: self._send_from_worker(
                    "onSpriteStatus", {"message": message}
                )
            )
            thread.finished_signal.connect(
                lambda success, message: completion.update(
                    success=success, message=message
                ),
                Qt.ConnectionType.DirectConnection,
            )
            thread.finished.connect(
                lambda: mw.taskman.run_on_main(
                    lambda: self._finish_sprite_update(thread, completion)
                )
            )
            thread.start()
        except Exception as error:
            self._sprite_thread = None
            self._send(
                "onUpdateOperation",
                {"kind": "sprites-install", "busy": False},
            )
            self._report_code_error(error)
            return {"success": False, "message": str(error)}
        return {"pending": True}

    def defer_close_for_sprite_update(self):
        thread = self._sprite_thread
        if thread is None or not thread.isRunning():
            return False
        self._close_after_sprite_update = True
        thread.cancel()
        self._send("onSpriteStatus", {"message": "Cancelling sprite update..."})
        return True

    def _finish_sprite_update(self, thread, completion):
        success = completion.get("success", False)
        message = completion.get(
            "message", "Sprite update stopped unexpectedly. Please try again."
        )
        try:
            from ..functions.sprite_functions import _clear_sprite_cache

            _clear_sprite_cache()
        except Exception as error:
            self._report_code_error(error)
        if success:
            try:
                manifest_path = (
                    Path(user_path_sprites).parent / "sprites_local_manifest.json"
                )
                if manifest_path.exists():
                    manifest_path.unlink()
            except OSError as error:
                self._report_code_error(error)
            self._sprite_diff = None
        try:
            self._send(
                "onSpritesFinished",
                {"success": success, "message": message},
            )
        finally:
            if self._sprite_thread is thread:
                self._sprite_thread = None
            self._send(
                "onUpdateOperation",
                {"kind": "sprites-install", "busy": False},
            )
            if self._close_after_sprite_update:
                self._close_after_sprite_update = False
                self._window.close()
