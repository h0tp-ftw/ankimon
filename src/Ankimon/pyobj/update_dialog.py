from aqt import mw
from aqt.operations import QueryOp
from pathlib import Path
from aqt.qt import (
    Qt,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QProgressBar,
    QTabWidget,
    QWidget,
    QMessageBox,
    QGroupBox,
    QFrame,
    QSizePolicy,
    QSpacerItem,
    QTextBrowser,
    QCheckBox,
)
from aqt.theme import theme_manager

from .update_manager import (
    fetch_releases,
    fetch_tags,
    fetch_branches,
    fetch_open_prs,
    apply_update,
    is_git_clone,
    get_git_checkout_info,
    git_checkout_source,
    _download_zip_to_temp,
    _download_branch_zip,
    _download_pr_zip,
    read_update_state,
    fetch_branch_sha,
    published_at_for_tag,
    stamp_addon_mod,
)
from ..resources import addon_ver, IS_EXPERIMENTAL_BUILD


def _start_query_op(parent, op, success, failure):
    try:
        QueryOp(
            parent=parent, op=op, success=success
        ).failure(failure).without_collection().run_in_background()
    except Exception as exc:
        # Submission happens on the Qt thread, so synchronous failures can use
        # the same UI-safe cleanup callback as background worker failures.
        failure(exc)


class UpdateDialog(QDialog):
    def __init__(self, parent=None, select_tab=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Update Ankimon")
        self.setMinimumWidth(520)
        self.resize(560, 460)

        self._releases = []
        self._tags = []
        self._branches = []
        self._prs = []
        self.dev_data_loaded = False
        self._busy_operations = set()
        self._action_button_states = {}
        self._closing = False
        self._close_finalized = False
        self._sprites_busy_token = None
        self.sprites_thread = None
        self._git_clone = is_git_clone()
        self._git_info = get_git_checkout_info() if self._git_clone else {}

        self._apply_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_header())

        body = QVBoxLayout()
        body.setSpacing(12)
        body.setContentsMargins(20, 16, 20, 16)

        body.addLayout(self._build_channel_row())
        if self._git_clone:
            body.addWidget(self._build_git_notice())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_brrr_tab(), f"  Branch: {self.active_branch}  ")
        self.tabs.addTab(self._build_releases_tab(), "  Releases  ")
        self.tabs.addTab(self._build_dev_tab(), "  Developer  ")
        self.tabs.addTab(self._build_sprites_tab(), "  Sprites  ")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        body.addWidget(self.tabs)

        if select_tab == "sprites":
            self.tabs.setCurrentIndex(3)
        elif self._git_clone:
            self.tabs.setCurrentIndex(2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(self.progress_bar.fontMetrics().height() + 8)
        body.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {self._colors['text']}; padding: 2px 4px;"
        )
        self.status_label.setMinimumHeight(24)
        body.addWidget(self.status_label)

        layout.addLayout(body)
        self._load_data()

    def _build_git_notice(self):
        c = self._colors
        info = self._git_info
        branch = info.get("branch") or "unknown"
        sha = info.get("sha") or "unknown"
        detached = branch == "HEAD"
        display_branch = "detached checkout" if detached else branch
        state = "local changes" if info.get("dirty") else "clean"
        state_color = c["warning"] if info.get("dirty") else c["success"]

        group = QGroupBox("Git Workspace Mode")
        group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {c['header_bg']};
                border: 2px solid {c['accent']};
                border-radius: 10px;
                margin-top: 10px;
                padding: 18px 12px 12px 12px;
            }}
            QGroupBox::title {{
                color: {c['accent']};
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                font-weight: bold;
            }}
        """)
        row = QHBoxLayout(group)

        note = QLabel(
            f"<b>{display_branch}</b> · <code>{sha}</code> · "
            f"<span style='color:{state_color}'><b>{state}</b></span><br>"
            "Use the same Releases and Developer tabs below. Git fetches the "
            "selected source and checks it out without resetting local branches."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {c['text']};")
        row.addWidget(note, 1)

        self.git_pull_btn = QPushButton("Fast-forward Current Branch")
        # Route through _set_action_enabled so _action_button_states records the
        # intended state: _end_busy restores from that map, and a plain
        # setEnabled() would let the button come back enabled after an update
        # even on a detached or dirty checkout.
        self._set_action_enabled(
            self.git_pull_btn, not detached and not info.get("dirty")
        )
        self.git_pull_btn.setToolTip(
            "Unavailable while detached or while the checkout has local changes."
            if not self.git_pull_btn.isEnabled()
            else "Run git pull --ff-only on the current branch."
        )
        self.git_pull_btn.clicked.connect(
            lambda: self._run_update(
                None,
                "current Git branch",
                source_type="current",
                source_name="current",
            )
        )
        row.addWidget(self.git_pull_btn)
        return group

    def _build_channel_row(self):
        """A labeled dropdown to pick the auto-update channel (dialog-only UI).
        Persists the choice immediately via update_manager.set_update_channel."""
        from .update_manager import (
            get_update_channel,
            set_update_channel,
            CHANNEL_STABLE,
            CHANNEL_EXPERIMENTAL,
            CHANNEL_MAIN,
        )

        row = QHBoxLayout()
        label = QLabel("Auto-update channel:")
        label.setStyleSheet("font-size: 12px;")
        row.addWidget(label)

        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Stable", CHANNEL_STABLE)
        self.channel_combo.addItem("Experimental (-E)", CHANNEL_EXPERIMENTAL)
        self.channel_combo.addItem("Main (bleeding edge)", CHANNEL_MAIN)
        idx = self.channel_combo.findData(get_update_channel())
        if idx >= 0:
            self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.currentIndexChanged.connect(
            lambda _i: set_update_channel(self.channel_combo.currentData())
        )
        row.addWidget(self.channel_combo)
        row.addStretch()
        return row

    @property
    def active_branch(self) -> str:
        if self._git_clone:
            branch = self._git_info.get("branch") or "main"
            return "detached" if branch == "HEAD" else branch
        state = read_update_state()
        if state and state.get("source_type") == "branch":
            return state.get("source_name") or "main"
        return "main"

    def _apply_theme(self):
        is_dark = theme_manager.night_mode
        if is_dark:
            self._colors = {
                "bg": "#2b2b2b",
                "header_bg": "#1e1e1e",
                "text": "#e0e0e0",
                "muted": "#888888",
                "accent": "#4fc3f7",
                "success": "#66bb6a",
                "warning": "#ffa726",
                "error": "#ef5350",
                "group_bg": "#333333",
                "group_border": "#444444",
                "btn_bg": "#3d3d3d",
                "btn_hover": "#505050",
                "btn_primary": "#1976d2",
                "btn_primary_hover": "#1565c0",
                "progress_text": "#ffffff",
                "progress_chunk": "#1565c0",
            }
        else:
            self._colors = {
                "bg": "#ffffff",
                "header_bg": "#f5f5f5",
                "text": "#212121",
                "muted": "#757575",
                "accent": "#1976d2",
                "success": "#2e7d32",
                "warning": "#e65100",
                "error": "#c62828",
                "group_bg": "#fafafa",
                "group_border": "#e0e0e0",
                "btn_bg": "#eeeeee",
                "btn_hover": "#e0e0e0",
                "btn_primary": "#1976d2",
                "btn_primary_hover": "#1565c0",
                "progress_text": "#212121",
                "progress_chunk": "#90caf9",
            }
        c = self._colors
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c["bg"]};
            }}
            QGroupBox {{
                background-color: {c["group_bg"]};
                border: 1px solid {c["group_border"]};
                border-radius: 8px;
                margin-top: 8px;
                padding: 16px 12px 12px 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }}
            QComboBox {{
                padding: 6px 10px;
                border: 1px solid {c["group_border"]};
                border-radius: 6px;
                background-color: {c["btn_bg"]};
                min-height: 24px;
            }}
            QComboBox:hover {{
                border-color: {c["accent"]};
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {c["group_border"]};
                background-color: {c["bg"]};
                selection-background-color: {c["accent"]};
            }}
            QPushButton {{
                padding: 8px 16px;
                border: 1px solid {c["group_border"]};
                border-radius: 6px;
                background-color: {c["btn_bg"]};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {c["btn_hover"]};
            }}
            QPushButton:disabled {{
                color: {c["muted"]};
            }}
            QProgressBar {{
                border: none;
                background-color: {c["group_border"]};
                border-radius: 4px;
                color: {c["progress_text"]};
                text-align: center;
                font-weight: bold;
                padding: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {c["progress_chunk"]};
                border-radius: 4px;
            }}
            QTabWidget::pane {{
                border: 1px solid {c["group_border"]};
                border-radius: 8px;
                background-color: {c["bg"]};
            }}
            QTabBar::tab {{
                padding: 8px 16px;
                color: {c["text"]};
                border: 1px solid transparent;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {c["bg"]};
                border-color: {c["group_border"]};
            }}
            QTabBar::tab:!selected {{
                background-color: {c["btn_bg"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {c["btn_hover"]};
            }}
        """)

    def _build_header(self):
        c = self._colors
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {c["header_bg"]};
                border-bottom: 1px solid {c["group_border"]};
            }}
        """)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(20, 16, 20, 14)
        frame_layout.setSpacing(4)

        title = QLabel("Update Ankimon")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {c['text']}; background: transparent; border: none;"
        )
        frame_layout.addWidget(title)

        state = read_update_state()
        ver_text = f"Installed: {addon_ver}"
        if state:
            source_type = state.get("source_type")
            source_name = state.get("source_name")
            commit_sha = state.get("commit_sha", "")
            sha_short = f" ({commit_sha[:7]})" if commit_sha else ""
            if source_type == "branch":
                ver_text = f"Installed: {addon_ver} (Branch: {source_name}{sha_short})"
            elif source_type == "pr":
                ver_text = f"Installed: {addon_ver} (PR #{source_name}{sha_short})"
            elif source_type == "tag":
                ver_text = f"Installed: {addon_ver} (Tag: {source_name})"
            elif source_type == "release":
                ver_text = f"Installed: {addon_ver} (Release: {source_name})"
        else:
            if IS_EXPERIMENTAL_BUILD:
                ver_text = f"Installed: {addon_ver} (Experimental Build)"

        ver = QLabel(ver_text)
        ver.setStyleSheet(
            f"font-size: 12px; color: {c['muted']}; background: transparent; border: none;"
        )
        frame_layout.addWidget(ver)

        return frame

    def _build_brrr_tab(self):
        c = self._colors
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(10, 14, 10, 10)

        # Info & Details Group
        self.brrr_details_group = QGroupBox(f"Active Branch: {self.active_branch}")
        details_layout = QVBoxLayout(self.brrr_details_group)
        details_layout.setSpacing(6)

        self.brrr_installed_commit_label = QLabel("Installed Commit: Loading...")
        self.brrr_installed_commit_label.setStyleSheet(
            "font-size: 12px; font-weight: normal;"
        )
        details_layout.addWidget(self.brrr_installed_commit_label)

        self.brrr_commit_date_label = QLabel("Commit Date: Loading...")
        self.brrr_commit_date_label.setStyleSheet(
            "font-size: 12px; font-weight: normal;"
        )
        details_layout.addWidget(self.brrr_commit_date_label)

        self.brrr_last_update_label = QLabel("Last Update Installed: Loading...")
        self.brrr_last_update_label.setStyleSheet(
            "font-size: 12px; font-weight: normal;"
        )
        details_layout.addWidget(self.brrr_last_update_label)

        self.brrr_status_label = QLabel("Status: Checking branch...")
        self.brrr_status_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['muted']};"
        )
        details_layout.addWidget(self.brrr_status_label)

        layout.addWidget(self.brrr_details_group)

        # Commits Feed
        commits_group = QGroupBox("Recent Branch Updates")
        commits_layout = QVBoxLayout(commits_group)
        commits_layout.setSpacing(6)

        self.brrr_commits_box = QTextBrowser()
        self.brrr_commits_box.setReadOnly(True)
        self.brrr_commits_box.setOpenExternalLinks(True)
        self.brrr_commits_box.setMinimumHeight(110)
        self.brrr_commits_box.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {c["bg"]};
                border: 1px solid {c["group_border"]};
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
                color: {c["text"]};
            }}
        """)
        self.brrr_commits_box.setHtml(
            "<font color='gray'>Checking for changes...</font>"
        )
        commits_layout.addWidget(self.brrr_commits_box)

        layout.addWidget(commits_group)

        # Snooze and Controls Bar
        ctrl_layout = QHBoxLayout()
        self.brrr_snooze_checkbox = QCheckBox("Snooze notifications for 1 week")
        self.brrr_snooze_checkbox.setStyleSheet(f"color: {c['text']}; font-size: 12px;")
        self.brrr_snooze_checkbox.stateChanged.connect(self._on_brrr_snooze_changed)
        ctrl_layout.addWidget(self.brrr_snooze_checkbox)
        ctrl_layout.addStretch()

        self.brrr_update_btn = QPushButton("Update Experimental Branch")
        self.brrr_update_btn.setMinimumHeight(38)
        self.brrr_update_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {c["btn_primary"]};
                color: white;
                font-weight: bold;
                font-size: 12px;
                border: none;
                border-radius: 6px;
                min-width: 180px;
            }}
            QPushButton:hover {{ background-color: {c["btn_primary_hover"]}; }}
            QPushButton:disabled {{ background-color: {c["btn_bg"]}; color: {c["muted"]}; }}
        """)
        self._set_action_enabled(self.brrr_update_btn, False)
        self.brrr_update_btn.clicked.connect(self._on_brrr_update_clicked)
        ctrl_layout.addWidget(self.brrr_update_btn)

        layout.addLayout(ctrl_layout)
        return widget

    def _populate_brrr_ui(self, state, remote_sha, local_commit_date, commits):
        c = self._colors
        import time
        import html

        # Update dynamic active branch labels
        active = self.active_branch
        self.brrr_details_group.setTitle(f"Active Branch: {active}")
        self.tabs.setTabText(0, f"  Branch: {active}  ")

        # 1. Local Commit SHA ("or" also covers a null/empty value persisted in
        # the user-editable update_state.json, where .get() defaults would not)
        local_sha = state.get("commit_sha") or "unknown"
        local_sha_short = local_sha[:7] if len(local_sha) >= 7 else local_sha
        self.brrr_installed_commit_label.setText(
            f"Installed Commit:  <b>{local_sha_short}</b>"
        )

        # 2. Commit Date
        if local_commit_date:
            date_clean = local_commit_date.replace("T", " ").replace("Z", "")
            self.brrr_commit_date_label.setText(
                f"Commit Date:  <b>{date_clean} UTC</b>"
            )
        else:
            self.brrr_commit_date_label.setText(
                "Commit Date:  <b>Unknown (first update will fetch this)</b>"
            )

        # 3. Last Updated On
        installed_at = state.get("installed_at")
        if not isinstance(installed_at, (int, float)):
            installed_at = None
        if not installed_at:
            try:
                from .update_manager import get_update_state_path

                state_path = get_update_state_path()
                if state_path.exists():
                    installed_at = state_path.stat().st_mtime
            except Exception:
                pass

        if installed_at:
            date_formatted = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(installed_at)
            )
            self.brrr_last_update_label.setText(
                f"Last Update Installed:  <b>{date_formatted}</b>"
            )
        else:
            self.brrr_last_update_label.setText(
                "Last Update Installed:  <b>Never (Perform update to record)</b>"
            )

        # 4. Snooze Checkbox (tolerate a null/non-numeric skip_until in the
        # user-editable state file; keep in sync with changelog.check_branch_update)
        skip_until = state.get("skip_until")
        is_snoozed = isinstance(skip_until, (int, float)) and skip_until > time.time()
        self.brrr_snooze_checkbox.blockSignals(True)
        self.brrr_snooze_checkbox.setChecked(is_snoozed)
        self.brrr_snooze_checkbox.blockSignals(False)

        # 5. Status & Update Button
        if not remote_sha:
            self.brrr_status_label.setText("Status:  Could not check connection.")
            self.brrr_status_label.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {c['error']};"
            )
            self._set_action_enabled(self.brrr_update_btn, False)
        elif local_sha != remote_sha:
            self.brrr_status_label.setText(
                f"Status:  New Update Available! (Latest: {remote_sha[:7]})"
            )
            self.brrr_status_label.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {c['warning']};"
            )
            self._set_action_enabled(self.brrr_update_btn, True)
            self.brrr_update_btn.setText("Update Branch Now")
        else:
            self.brrr_status_label.setText("Status:  Up to date!")
            self.brrr_status_label.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {c['success']};"
            )
            self._set_action_enabled(self.brrr_update_btn, False)
            self.brrr_update_btn.setText("Already Up to Date")

        # 6. Commits Feed
        if commits:
            accent_color = c["accent"]
            html_content = f"<b>What's New on {active} Branch:</b><br><ul style='margin-top: 4px; margin-bottom: 4px; padding-left: 20px;'>"
            for commit in commits:
                sha = commit.get("sha", "")
                msg = commit.get("message", "")
                msg_escaped = html.escape(msg)
                html_content += f"<li style='margin-bottom: 4px;'><code><font color='{accent_color}'>{sha}</font></code> - {msg_escaped}</li>"
            html_content += "</ul>"
            self.brrr_commits_box.setHtml(html_content)
        else:
            self.brrr_commits_box.setHtml(
                "<font color='gray'>No new commit messages fetched.</font>"
            )

    def _on_brrr_snooze_changed(self, _state):
        import time
        from .update_manager import set_update_skip_until

        if self.brrr_snooze_checkbox.isChecked():
            one_week_later = time.time() + 604800
            set_update_skip_until(one_week_later)
        else:
            set_update_skip_until(0)

    def _on_brrr_update_clicked(self):
        branch = self.active_branch
        self._run_update(
            lambda progress_cb: _download_branch_zip(branch, progress_cb),
            f"latest {branch}",
            source_type="branch",
            source_name=branch,
        )

    def _build_releases_tab(self):
        c = self._colors
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)
        layout.setContentsMargins(6, 14, 6, 6)

        # Quick update
        latest_group = QGroupBox("Quick Update")
        latest_layout = QVBoxLayout(latest_group)
        latest_layout.setSpacing(10)

        desc = QLabel("One click to get the latest experimental release.")
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color: {c['muted']}; font-size: 11px; font-weight: normal;"
        )
        latest_layout.addWidget(desc)

        self.latest_tag_label = QLabel("Checking...")
        self.latest_tag_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {c['muted']};"
        )
        latest_layout.addWidget(self.latest_tag_label)

        self.update_latest_btn = QPushButton("Update to Latest Release")
        self.update_latest_btn.setMinimumHeight(42)
        self.update_latest_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {c["btn_primary"]};
                color: white;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {c["btn_primary_hover"]}; }}
            QPushButton:disabled {{ background-color: {c["btn_bg"]}; color: {c["muted"]}; }}
        """)
        self.update_latest_btn.clicked.connect(self._on_latest_release_update)
        self._set_action_enabled(self.update_latest_btn, False)
        latest_layout.addWidget(self.update_latest_btn)
        layout.addWidget(latest_group)

        # Specific release
        specific_group = QGroupBox("Specific Release")
        specific_layout = QVBoxLayout(specific_group)
        specific_layout.setSpacing(8)

        pick_label = QLabel("Choose a version:")
        pick_label.setStyleSheet(
            f"color: {c['muted']}; font-size: 11px; font-weight: normal;"
        )
        specific_layout.addWidget(pick_label)

        self.release_combo = QComboBox()
        self.release_combo.addItem("Loading...")
        specific_layout.addWidget(self.release_combo)

        self.release_btn = QPushButton("Install Selected Release")
        self.release_btn.setMinimumHeight(34)
        self.release_btn.clicked.connect(self._on_release_update)
        self._set_action_enabled(self.release_btn, False)
        specific_layout.addWidget(self.release_btn)
        layout.addWidget(specific_group)

        layout.addStretch()
        return widget

    def _build_dev_tab(self):
        c = self._colors
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)
        layout.setContentsMargins(6, 14, 6, 6)

        info = QLabel("Install code directly from branches, pull requests, or tags.")
        info.setStyleSheet(f"color: {c['muted']}; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        warning_text = (
            "Git workspace mode is active. Sources selected here are fetched from "
            "the official Ankimon repository and checked out with Git; local "
            "changes must be committed, stashed, or discarded first."
            if self._git_clone
            else
            "⚠ Pull requests and development branches may contain unreviewed code. "
            "Only install sources you trust. Your Pokémon data and sprites are "
            "preserved during archive-based updates."
        )
        warning = QLabel(warning_text)
        warning.setStyleSheet(
            f"color: {c['warning']}; font-size: 11px; font-weight: bold;"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        group = QGroupBox("Install from Source")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(10)

        source_label = QLabel("Source type:")
        source_label.setStyleSheet("font-weight: normal; font-size: 12px;")
        group_layout.addWidget(source_label)

        self.source_combo = QComboBox()
        self.source_combo.addItem("Latest Main Branch", "main")
        self.source_combo.addItem("Pull Request", "pr")
        self.source_combo.addItem("Branch", "branch")
        self.source_combo.addItem("Tag", "tag")
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        group_layout.addWidget(self.source_combo)

        self.target_label = QLabel("")
        self.target_label.setStyleSheet("font-weight: normal; font-size: 12px;")
        self.target_label.setVisible(False)
        group_layout.addWidget(self.target_label)

        self.target_combo = QComboBox()
        self.target_combo.setVisible(False)
        group_layout.addWidget(self.target_combo)

        group_layout.addSpacerItem(QSpacerItem(0, 6))

        self.dev_install_btn = QPushButton("Install")
        self.dev_install_btn.setMinimumHeight(38)
        self.dev_install_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {c["btn_primary"]};
                color: white;
                font-weight: bold;
                font-size: 12px;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {c["btn_primary_hover"]}; }}
            QPushButton:disabled {{ background-color: {c["btn_bg"]}; color: {c["muted"]}; }}
        """)
        self.dev_install_btn.clicked.connect(self._on_dev_install)
        group_layout.addWidget(self.dev_install_btn)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _build_sprites_tab(self):
        c = self._colors
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)
        layout.setContentsMargins(6, 14, 6, 6)

        info = QLabel("Check and download updates for the Ankimon sprites repository.")
        info.setStyleSheet(f"color: {c['muted']}; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.sprites_status = QLabel("Ready to check for updates.")
        self.sprites_status.setStyleSheet("font-size: 12px;")
        self.sprites_status.setWordWrap(True)
        layout.addWidget(self.sprites_status)

        self.sprites_progress = QProgressBar()
        self.sprites_progress.setRange(0, 100)
        self.sprites_progress.setValue(0)
        self.sprites_progress.setVisible(False)
        self.sprites_progress.setFixedHeight(12)
        layout.addWidget(self.sprites_progress)

        self.sprites_snooze_checkbox = QCheckBox("Snooze these updates for 7 days")
        self.sprites_snooze_checkbox.setStyleSheet(f"color: {c['muted']}; font-size: 11px;")
        
        from ..resources import user_path_sprites
        import json
        import time
        dest_dir = Path(user_path_sprites)
        state_path = dest_dir.parent / "sprites_update_state.json"
        is_snoozed = False
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                snooze_until = state_data.get("snooze_until")
                is_snoozed = isinstance(snooze_until, (int, float)) and time.time() < snooze_until
            except Exception:
                pass
        self.sprites_snooze_checkbox.setChecked(is_snoozed)
        self.sprites_snooze_checkbox.stateChanged.connect(self._on_sprites_snooze_changed)
        layout.addWidget(self.sprites_snooze_checkbox)

        btn_layout = QHBoxLayout()
        self.sprites_check_btn = QPushButton("Check for Updates")
        self.sprites_check_btn.setMinimumHeight(38)
        self.sprites_check_btn.clicked.connect(self._check_sprites)
        btn_layout.addWidget(self.sprites_check_btn)

        self.sprites_update_btn = QPushButton("Install Update")
        self.sprites_update_btn.setMinimumHeight(38)
        self.sprites_update_btn.setVisible(False)
        self.sprites_update_btn.clicked.connect(self._start_sprites_download)
        btn_layout.addWidget(self.sprites_update_btn)

        layout.addLayout(btn_layout)
        layout.addStretch()
        return widget

    def _on_sprites_snooze_changed(self, _state):
        from ..resources import user_path_sprites
        import json
        import time
        dest_dir = Path(user_path_sprites)
        state_path = dest_dir.parent / "sprites_update_state.json"
        
        state_data = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
                
        if self.sprites_snooze_checkbox.isChecked():
            state_data["snooze_until"] = time.time() + 7 * 24 * 60 * 60
        else:
            state_data["snooze_until"] = 0
            
        try:
            state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _check_sprites(self):
        from .sprite_updater import calculate_sprite_diff
        from ..resources import user_path_sprites

        busy_token = self._begin_busy()
        self.sprites_status.setText("Checking for sprite updates...")
        self.sprites_progress.setValue(0)
        self.sprites_progress.setVisible(False)
        self.sprites_update_btn.setVisible(False)

        dest_dir = Path(user_path_sprites)

        def bg(_col):
            # Run with ignore_snooze=True since this is a manual check
            return calculate_sprite_diff(dest_dir, silent=False, ignore_snooze=True)

        def settle_busy():
            self._end_busy(busy_token)

        def done(result):
            try:
                status = result.get("status")
                if status == "up_to_date":
                    self.sprites_status.setText("Sprites are already up to date!")
                    self.sprites_progress.setValue(100)
                    self.sprites_progress.setVisible(True)
                elif status == "error":
                    self.sprites_status.setText(
                        f"Error checking updates: {result.get('error')}"
                    )
                elif status == "update_available":
                    self.sprites_added = result.get("added", [])
                    self.sprites_modified = result.get("modified", [])
                    self.sprites_deleted = result.get("deleted", [])
                    self.sprites_remote_sha = result.get("remote_sha")

                    msg = "A sprites update is available!\n\n"
                    msg += f"  • New sprites: {len(self.sprites_added)}\n"
                    msg += f"  • Modified sprites: {len(self.sprites_modified)}\n"
                    if self.sprites_deleted:
                        msg += f"  • Obsolete to remove: {len(self.sprites_deleted)}\n"

                    self.sprites_status.setText(msg)
                    self.sprites_update_btn.setVisible(True)
            finally:
                settle_busy()

        def failed(exc):
            settle_busy()
            self.sprites_status.setText(f"Error checking sprite updates: {exc}")

        _start_query_op(self, bg, done, failed)

    def _start_sprites_download(self):
        if self.sprites_thread is not None and self.sprites_thread.isRunning():
            return

        from .sprite_updater import SpriteUpdateDiffThread
        from ..resources import user_path_sprites

        dest_dir = Path(user_path_sprites)
        busy_token = self._begin_busy()
        self._sprites_busy_token = busy_token
        self.sprites_progress.setVisible(True)
        self.sprites_progress.setValue(0)
        completion_result = None
        thread = None

        def settle_busy():
            if busy_token in self._busy_operations:
                self._end_busy(busy_token)
            if self._sprites_busy_token is busy_token:
                self._sprites_busy_token = None

        def record_finished(success, message):
            nonlocal completion_result
            completion_result = (success, message)

        def thread_stopped():
            closing = self._closing
            try:
                if not closing and self.sprites_thread is thread:
                    if completion_result is None:
                        self.sprites_status.setText(
                            "Sprite update stopped unexpectedly. Please try again."
                        )
                    else:
                        success, message = completion_result
                        self.sprites_update_btn.setVisible(False)
                        if success:
                            try:
                                manifest_path = (
                                    dest_dir.parent / "sprites_local_manifest.json"
                                )
                                if manifest_path.exists():
                                    manifest_path.unlink()
                            except Exception:
                                pass
                            self.sprites_status.setText("Update complete! " + message)
                            self.sprites_progress.setValue(100)
                        else:
                            self.sprites_status.setText("Update failed: " + message)
            finally:
                settle_busy()
                if self.sprites_thread is thread:
                    self.sprites_thread = None
            if closing and not self._close_finalized:
                self.reject()

        def update_progress(value):
            if (
                not self._closing
                and self.sprites_thread is thread
                and busy_token in self._busy_operations
            ):
                self.sprites_progress.setValue(value)

        def update_status(message):
            if (
                not self._closing
                and self.sprites_thread is thread
                and busy_token in self._busy_operations
            ):
                self.sprites_status.setText(message)

        try:
            thread = SpriteUpdateDiffThread(
                self.sprites_added,
                self.sprites_modified,
                self.sprites_deleted,
                self.sprites_remote_sha,
                dest_dir,
            )
            self.sprites_thread = thread
            thread.progress_signal.connect(
                lambda value: mw.taskman.run_on_main(lambda: update_progress(value))
            )
            thread.status_signal.connect(
                lambda message: mw.taskman.run_on_main(lambda: update_status(message))
            )
            thread.finished_signal.connect(
                record_finished, Qt.ConnectionType.DirectConnection
            )
            thread.finished.connect(lambda: mw.taskman.run_on_main(thread_stopped))
            thread.start()
        except Exception as exc:
            settle_busy()
            if self.sprites_thread is thread:
                self.sprites_thread = None
            self.sprites_status.setText(f"Could not start sprite update: {exc}")

    def _defer_close_for_sprite_thread(self):
        self._closing = True
        if self.sprites_thread is not None and self.sprites_thread.isRunning():
            self.sprites_thread.cancel()
            self.sprites_status.setText("Cancelling sprite update...")
            return True

        if self._sprites_busy_token is not None:
            token = self._sprites_busy_token
            self._sprites_busy_token = None
            self._end_busy(token)
        return False

    def reject(self):
        if self._defer_close_for_sprite_thread():
            return
        self._close_finalized = True
        super().reject()

    def closeEvent(self, event):
        if self._defer_close_for_sprite_thread():
            event.ignore()
            return
        self._close_finalized = True
        super().closeEvent(event)

    # --- Data loading ---

    def _on_source_changed(self, index):
        source = self.source_combo.currentData()
        show = source != "main"
        self.target_label.setVisible(show)
        self.target_combo.setVisible(show)
        if show:
            self._populate_target(source)

    def _populate_target(self, source):
        self.target_combo.clear()
        if source == "pr":
            self.target_label.setText("Pull request:")
            if self._prs:
                for pr in self._prs:
                    self.target_combo.addItem(f"#{pr['number']} — {pr['title']}", pr)
            else:
                self.target_combo.addItem("No open PRs")
        elif source == "branch":
            self.target_label.setText("Branch:")
            if self._branches:
                default_idx = 0
                for idx, b in enumerate(self._branches):
                    self.target_combo.addItem(b["name"], b)
                    if b["name"] == "main":
                        default_idx = idx
                self.target_combo.setCurrentIndex(default_idx)
            else:
                self.target_combo.addItem("No branches found")
        elif source == "tag":
            self.target_label.setText("Tag:")
            if self._tags:
                for t in self._tags:
                    self.target_combo.addItem(t["name"], t)
            else:
                self.target_combo.addItem("No tags found")

    def _load_data(self):
        busy_token = self._begin_busy()
        self.status_label.setText("Checking for updates...")

        def bg(_col):
            from .update_manager import (
                fetch_branch_sha,
                fetch_commit_date,
                fetch_branch_commits,
            )

            # 1. Fetch releases
            releases = []
            try:
                releases = fetch_releases()
            except Exception:
                pass

            # 2. Get local state
            state = read_update_state() or {}
            local_sha = state.get("commit_sha")
            branch = state.get("source_name") or "main"

            # 3. Fetch remote branch details
            remote_sha = None
            try:
                remote_sha = fetch_branch_sha(branch)
            except Exception:
                pass

            local_commit_date = None
            if local_sha:
                try:
                    local_commit_date = fetch_commit_date(local_sha)
                except Exception:
                    pass

            # 4. Fetch last 5 commits on branch
            commits = []
            try:
                commits = fetch_branch_commits(branch, local_sha)
            except Exception:
                pass

            return releases, state, remote_sha, local_commit_date, commits

        def on_done(result):
            try:
                self._releases, state, remote_sha, local_commit_date, commits = result
                self._populate_brrr_ui(state, remote_sha, local_commit_date, commits)
                self._populate_ui()
            finally:
                self._end_busy(busy_token)

        def on_failed(exc):
            if self._end_busy(busy_token):
                self.status_label.setText(f"Could not check for updates: {exc}")

        _start_query_op(self, bg, on_done, on_failed)

    def _on_tab_changed(self, index):
        if index == 2 and not self.dev_data_loaded:
            self._load_dev_data()

    def _load_dev_data(self):
        busy_token = self._begin_busy()
        self.status_label.setText("Loading developer options...")

        def bg(_col):
            tags = []
            try:
                tags = fetch_tags()
            except Exception:
                pass
            branches = []
            try:
                branches = fetch_branches()
            except Exception:
                pass
            prs = []
            try:
                prs = fetch_open_prs()
            except Exception:
                pass
            return (tags, branches, prs)

        def on_done(result):
            try:
                self._tags, self._branches, self._prs = result
                self.dev_data_loaded = True

                # Repopulate targets in the Developer tab UI if needed
                source = self.source_combo.currentData()
                if source and source not in ("branch_brrr", "main"):
                    self._populate_target(source)
            finally:
                self._end_busy(busy_token)

        def on_failed(exc):
            if self._end_busy(busy_token):
                self.status_label.setText(
                    f"Could not load developer options: {exc}"
                )

        _start_query_op(self, bg, on_done, on_failed)

    def _populate_ui(self):
        c = self._colors
        if self._releases:
            latest = self._releases[0]["name"]
            if latest == addon_ver:
                self.latest_tag_label.setText(f"You're up to date  ({latest})")
                self.latest_tag_label.setStyleSheet(
                    f"font-weight: bold; font-size: 13px; color: {c['success']};"
                )
                self.update_latest_btn.setText("Already Up to Date")
                self._set_action_enabled(self.update_latest_btn, False)
            else:
                self.latest_tag_label.setText(f"New version available: {latest}")
                self.latest_tag_label.setStyleSheet(
                    f"font-weight: bold; font-size: 13px; color: {c['warning']};"
                )
                self._set_action_enabled(self.update_latest_btn, True)
        else:
            self.latest_tag_label.setText("Could not check for updates.")
            self.latest_tag_label.setStyleSheet(
                f"font-weight: bold; font-size: 13px; color: {c['error']};"
            )
            self._set_action_enabled(self.update_latest_btn, False)

        self.release_combo.clear()
        if self._releases:
            for r in self._releases:
                self.release_combo.addItem(r["name"], r)
            self._set_action_enabled(self.release_btn, True)
        else:
            self.release_combo.addItem("No releases found")
            self._set_action_enabled(self.release_btn, False)

        source = self.source_combo.currentData()
        if source and source != "main":
            self._populate_target(source)

    # --- Actions ---

    def _action_buttons(self):
        buttons = [
            self.brrr_update_btn,
            self.update_latest_btn,
            self.release_btn,
            self.dev_install_btn,
            self.sprites_check_btn,
            self.sprites_update_btn,
        ]
        # Only built in Git-checkout mode, so it must not be assumed present:
        # _begin_busy() runs on every install, Git or not.
        if hasattr(self, "git_pull_btn"):
            buttons.append(self.git_pull_btn)
        return tuple(buttons)

    def _set_action_enabled(self, button, enabled: bool):
        self._action_button_states[button] = enabled
        button.setEnabled(enabled and not self._busy_operations)

    def _begin_busy(self):
        token = object()
        was_idle = not self._busy_operations
        self._busy_operations.add(token)
        if was_idle:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
        for button in self._action_buttons():
            self._action_button_states.setdefault(button, button.isEnabled())
            button.setEnabled(False)
        return token

    def _end_busy(self, token):
        if token not in self._busy_operations:
            return False
        self._busy_operations.remove(token)
        if self._busy_operations:
            return False
        for button in self._action_buttons():
            button.setEnabled(self._action_button_states.get(button, False))
        self.progress_bar.setVisible(False)
        self.status_label.setText("")
        return True

    def _on_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            mw.taskman.run_on_main(lambda: self.progress_bar.setValue(percent))

    def _run_update(
        self,
        download_fn,
        label: str,
        source_type: str = None,
        source_name: str = None,
        commit_sha: str = None,
        published_at: str = None,
        extra_warning: str = None,
    ):
        if self._git_clone:
            prompt = (
                f"Switch this Git checkout to {label}?\n\n"
                "Your local branches and commits will not be reset. The checkout "
                "must be clean, and Anki must be restarted afterward."
            )
        else:
            prompt = (
                f"Update Ankimon to {label}?\n\n"
                "Your Pokemon data, settings, and sprites will be preserved."
            )
        if extra_warning:
            prompt = f"{extra_warning}\n\n{prompt}"
        confirm = QMessageBox.question(
            self,
            "Confirm Update",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        busy_token = self._begin_busy()
        self.status_label.setText(
            f"Preparing Git checkout for {label}..."
            if self._git_clone
            else f"Downloading {label}..."
        )

        def bg(_col):
            nonlocal commit_sha
            messages = []

            def status_update(m):
                messages.append(m)
                mw.taskman.run_on_main(lambda: self.status_label.setText(m))

            if self._git_clone:
                success, msg = git_checkout_source(
                    source_type or "current",
                    source_name,
                    status_cb=status_update,
                )
                # 4-tuple to match on_done's unpack; a Git checkout stamps no
                # pending addon mod, so pending_mod is None.
                return success, msg, messages, None

            if source_type == "branch" and not commit_sha:
                commit_sha = fetch_branch_sha(source_name)

            zip_path = download_fn(progress_cb=self._on_progress)
            if not zip_path:
                return (
                    False,
                    "Download failed. Check your internet connection.",
                    [],
                    None,
                )

            success, msg, pending_mod = apply_update(
                zip_path,
                source_type,
                source_name,
                commit_sha,
                published_at,
                status_cb=status_update,
            )
            return success, msg, messages, pending_mod

        def on_done(result):
            try:
                success, msg, messages, pending_mod = result
            except Exception as exc:
                on_failed(exc)
                return
            # Date meta.json here, not in the worker above: QueryOp guarantees
            # this callback runs on the main thread, which is the thread Anki
            # read-modify-writes meta.json from. Doing it in the worker would
            # let a stale snapshot overwrite a concurrent config change.
            if success and pending_mod:
                stamp_addon_mod(pending_mod)
            if self._end_busy(busy_token):
                self.status_label.setText(messages[-1] if messages else msg)
                self.progress_bar.setValue(100 if success else 0)
            if success:
                QMessageBox.information(
                    self,
                    "Update Complete",
                    f"{msg}\n\nPlease restart Anki for changes to take effect.",
                )
            else:
                QMessageBox.warning(self, "Update Failed", msg)

        def on_failed(exc):
            if self._end_busy(busy_token):
                self.status_label.setText(f"Update failed unexpectedly: {exc}")
                self.progress_bar.setValue(0)
            QMessageBox.warning(
                self,
                "Update Failed",
                f"The update stopped unexpectedly. Please try again.\n\n{exc}",
            )

        _start_query_op(self, bg, on_done, on_failed)

    def _on_latest_release_update(self):
        if not self._releases:
            return
        r = self._releases[0]
        self._run_update(
            lambda progress_cb: _download_zip_to_temp(r["zipball_url"], progress_cb),
            f"latest release ({r['name']})",
            source_type="release",
            source_name=r["name"],
            commit_sha=r["name"],
            published_at=r.get("published_at"),
        )

    def _on_release_update(self):
        data = self.release_combo.currentData()
        if data:
            self._run_update(
                lambda progress_cb: _download_zip_to_temp(
                    data["zipball_url"], progress_cb
                ),
                f"release {data['name']}",
                source_type="release",
                source_name=data["name"],
                commit_sha=data["name"],
                published_at=data.get("published_at"),
            )

    def _on_dev_install(self):
        source = self.source_combo.currentData()
        if source == "main":
            self._run_update(
                lambda progress_cb: _download_branch_zip("main", progress_cb),
                "latest main",
                source_type="branch",
                source_name="main",
            )
        elif source == "pr":
            data = self.target_combo.currentData()
            if data:
                self._run_update(
                    lambda progress_cb: _download_pr_zip(data["head_sha"], progress_cb),
                    f"PR #{data['number']} ({data['title']})",
                    source_type="pr",
                    source_name=str(data["number"]),
                    commit_sha=data["head_sha"],
                    extra_warning=(
                        "⚠ WARNING: Anyone can open a pull request. This code is "
                        "unreviewed and unreleased — installing it runs unverified "
                        "code on your computer. Only proceed if you trust this PR. "
                        "Use at your own risk."
                    ),
                )
        elif source == "branch":
            data = self.target_combo.currentData()
            if data:
                self._run_update(
                    lambda progress_cb: _download_branch_zip(data["name"], progress_cb),
                    f"branch {data['name']}",
                    source_type="branch",
                    source_name=data["name"],
                )
        elif source == "tag":
            data = self.target_combo.currentData()
            if data:
                self._run_update(
                    lambda progress_cb: _download_zip_to_temp(
                        data["zipball_url"], progress_cb
                    ),
                    f"tag {data['name']}",
                    source_type="tag",
                    source_name=data["name"],
                    commit_sha=data["name"],
                    # Every tag the picker offers names a published release, and
                    # installs byte-identical code to the Releases tab. Date it
                    # the same way, or the tag's (earlier) commit timestamp lets
                    # the AnkiWeb upload look newer than the code just installed.
                    published_at=published_at_for_tag(
                        data["name"], self._releases
                    ),
                )


class BranchUpdatePromptDialog(QDialog):
    def __init__(
        self, branch_name: str, remote_sha: str, commits: list[dict] = None, parent=None
    ):
        super().__init__(parent or mw)
        self.setWindowTitle("Ankimon Update Available")
        self.setMinimumWidth(460)
        if commits:
            self.resize(520, 420)
        else:
            self.resize(480, 240)

        is_dark = theme_manager.night_mode
        bg = "#2b2b2b" if is_dark else "#ffffff"
        text = "#e0e0e0" if is_dark else "#212121"
        border = "#444444" if is_dark else "#e0e0e0"
        btn_bg = "#3d3d3d" if is_dark else "#eeeeee"
        btn_hover = "#505050" if is_dark else "#e0e0e0"
        btn_primary = "#1976d2"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
            QLabel {{
                color: {text};
                font-size: 13px;
            }}
            QPushButton {{
                padding: 8px 16px;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {btn_bg};
                color: {text};
                font-size: 13px;
                min-width: 100px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel(f"<h3>Update Available for {branch_name}</h3>")
        layout.addWidget(title)

        desc = QLabel(
            f"A new update is available for your local <b>{branch_name}</b> branch.<br>"
            f"Latest Commit: <code>{remote_sha[:7]}</code>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if commits:
            commits_box = QTextBrowser()
            commits_box.setReadOnly(True)
            commits_box.setOpenExternalLinks(True)

            box_bg = "#333333" if is_dark else "#fafafa"
            box_border = "#444444" if is_dark else "#e0e0e0"
            accent_color = "#4fc3f7" if is_dark else "#1976d2"

            commits_box.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: {box_bg};
                    border: 1px solid {box_border};
                    border-radius: 6px;
                    padding: 8px;
                    font-size: 12px;
                    color: {text};
                }}
            """)

            import html

            html_content = "<b>What's New:</b><br><ul style='margin-top: 4px; margin-bottom: 4px; padding-left: 20px;'>"
            for c in commits:
                sha = c.get("sha", "")
                msg = c.get("message", "")
                msg_escaped = html.escape(msg)
                html_content += f"<li style='margin-bottom: 4px;'><code><font color='{accent_color}'>{sha}</font></code> - {msg_escaped}</li>"
            html_content += "</ul>"

            commits_box.setHtml(html_content)
            layout.addWidget(commits_box)

        prompt_label = QLabel(
            "Would you like to install the latest changes now?<br>"
            "Your Pokemon database, team, and settings will be preserved."
        )
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        self.skip_checkbox = QCheckBox("Don't notify me for 1 week")
        self.skip_checkbox.setStyleSheet(
            f"color: {text}; font-size: 12px; margin-top: 4px;"
        )
        layout.addWidget(self.skip_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_later = QPushButton("Later")
        self.btn_later.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_later)

        self.btn_update = QPushButton("Update Now")
        self.btn_update.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_primary};
                color: white;
                font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #1565c0;
            }}
        """)
        self.btn_update.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_update)

        layout.addLayout(btn_layout)

    def reject(self):
        if self.skip_checkbox.isChecked():
            import time
            from .update_manager import set_update_skip_until

            one_week_later = time.time() + 604800
            set_update_skip_until(one_week_later)

        QMessageBox.information(
            self,
            "Update Later",
            "No problem! You can always check for updates and install them later by going to Ankimon => Help => Check for Updates.",
        )
        super().reject()


class BranchUpdateProgressDialog(QDialog):
    def __init__(self, branch_name: str, remote_sha: str, parent=None, release: dict = None):
        super().__init__(parent or mw)
        self.setWindowTitle("Updating Ankimon")
        self.setMinimumWidth(440)
        self.resize(480, 200)

        self.branch_name = branch_name
        self.remote_sha = remote_sha
        # When set (a fetch_releases dict with name/zipball_url), install that
        # release instead of a branch zip — lets the release channels reuse this
        # same download/apply/progress flow.
        self.release = release

        is_dark = theme_manager.night_mode
        bg = "#2b2b2b" if is_dark else "#ffffff"
        text = "#e0e0e0" if is_dark else "#212121"
        muted = "#888888" if is_dark else "#757575"
        border = "#444444" if is_dark else "#e0e0e0"
        btn_bg = "#3d3d3d" if is_dark else "#eeeeee"
        btn_hover = "#505050" if is_dark else "#e0e0e0"
        progress_text = "#ffffff" if is_dark else "#212121"
        progress_chunk = "#1565c0" if is_dark else "#90caf9"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
            QLabel {{
                color: {text};
                font-size: 13px;
            }}
            QProgressBar {{
                border: none;
                background-color: {border};
                border-radius: 4px;
                text-align: center;
                color: {progress_text};
                font-weight: bold;
                padding: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {progress_chunk};
                border-radius: 4px;
            }}
            QPushButton {{
                padding: 8px 16px;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {btn_bg};
                color: {text};
                font-size: 13px;
                min-width: 100px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:disabled {{
                color: {muted};
                background-color: {btn_bg};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        self.status_label = QLabel(f"Preparing to update {branch_name}...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(self.progress_bar.fontMetrics().height() + 8)
        layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)
        self.update_started = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self.update_started:
            self.update_started = True
            self.start_update()

    def start_update(self):
        from .update_manager import (
            _download_branch_zip,
            _download_zip_to_temp,
            apply_update,
            stamp_addon_mod,
        )

        release = self.release
        if release:
            source_type, source_name, commit_sha = "release", release["name"], release["name"]
            download = lambda: _download_zip_to_temp(release["zipball_url"], progress_cb=self.on_progress)
        else:
            source_type, source_name, commit_sha = "branch", self.branch_name, self.remote_sha
            download = lambda: _download_branch_zip(self.branch_name, progress_cb=self.on_progress)
        published_at = release.get("published_at") if release else None

        def bg(_col):
            zip_path = download()
            if not zip_path:
                return False, "Download failed. Check your internet connection.", None

            def status_update(msg):
                mw.taskman.run_on_main(lambda: self.status_label.setText(msg))

            return apply_update(
                zip_path,
                source_type=source_type,
                source_name=source_name,
                commit_sha=commit_sha,
                published_at=published_at,
                status_cb=status_update,
            )

        def on_done(result):
            try:
                success, msg, pending_mod = result
            except Exception as exc:
                on_failed(exc)
                return
            # Main thread (QueryOp guarantees it), which is where meta.json has
            # to be written — see the matching note on the release/tag path.
            if success and pending_mod:
                stamp_addon_mod(pending_mod)
            self.btn_close.setEnabled(True)
            if success:
                self.btn_close.setText("Restart Anki")
                self.status_label.setText(
                    "Update applied successfully! Please restart Anki."
                )
                self.progress_bar.setValue(100)
                QMessageBox.information(
                    self,
                    "Update Complete",
                    f"{msg}\n\nPlease restart Anki for changes to take effect.",
                )
            else:
                self.status_label.setText(f"Update failed: {msg}")
                self.progress_bar.setValue(0)
                QMessageBox.warning(self, "Update Failed", msg)

        def on_failed(exc):
            self.btn_close.setEnabled(True)
            self.status_label.setText(
                "Update stopped unexpectedly. Please check your connection and try again."
            )
            self.progress_bar.setValue(0)
            QMessageBox.warning(
                self,
                "Update Failed",
                f"The update stopped unexpectedly. Please try again.\n\n{exc}",
            )

        _start_query_op(self, bg, on_done, on_failed)

    def on_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            mw.taskman.run_on_main(lambda: self.progress_bar.setValue(percent))


def show_branch_update_prompt(
    branch_name: str, remote_sha: str, commits: list[dict] = None
):
    dialog = BranchUpdatePromptDialog(branch_name, remote_sha, commits, mw)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        progress_dialog = BranchUpdateProgressDialog(branch_name, remote_sha, mw)
        progress_dialog.exec()


def show_release_update_prompt(channel: str, release: dict):
    """Auto-update nudge for the stable / experimental release channels.

    Shows the new version (and a snippet of its release notes) and, on accept,
    installs it through the shared progress dialog. "Later" plus the snooze
    checkbox defers for a week — mirroring the branch prompt's behaviour.
    """
    tag = release.get("name", "?")

    box = QMessageBox(mw)
    box.setWindowTitle("Ankimon Update Available")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(
        f"A new <b>{channel}</b> release of Ankimon is available: "
        f"<b>{tag}</b> (you have {addon_ver}).<br><br>"
        "Your Pokémon data, team, and settings will be preserved."
    )
    notes = (release.get("body") or "").strip()
    if notes:
        # Keep the popup compact; the full notes live on the GitHub release page.
        box.setInformativeText(notes[:800] + ("…" if len(notes) > 800 else ""))

    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    yes_btn = box.button(QMessageBox.StandardButton.Yes)
    yes_btn.setText("Update Now")
    box.button(QMessageBox.StandardButton.No).setText("Later")
    box.setDefaultButton(QMessageBox.StandardButton.Yes)

    snooze = QCheckBox("Don't notify me for 1 week")
    box.setCheckBox(snooze)

    box.exec()
    if box.clickedButton() is yes_btn:
        BranchUpdateProgressDialog(tag, tag, mw, release=release).exec()
    elif snooze.isChecked():
        import time
        from .update_manager import set_update_skip_until

        set_update_skip_until(time.time() + 604800)
