from aqt import mw
from aqt.operations import QueryOp
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
)
from aqt.theme import theme_manager

from .update_manager import (
    fetch_releases,
    fetch_tags,
    fetch_branches,
    fetch_open_prs,
    apply_update,
    _download_zip_to_temp,
    _download_branch_zip,
    _download_pr_zip,
)
from ..resources import addon_ver


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Update Ankimon")
        self.setMinimumWidth(520)
        self.resize(560, 460)

        self._releases = []
        self._tags = []
        self._branches = []
        self._prs = []

        self._apply_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_header())

        body = QVBoxLayout()
        body.setSpacing(12)
        body.setContentsMargins(20, 16, 20, 16)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_releases_tab(), "  Releases  ")
        self.tabs.addTab(self._build_dev_tab(), "  Developer  ")
        body.addWidget(self.tabs)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(8)
        body.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 11px; color: gray; padding: 0 4px;")
        self.status_label.setFixedHeight(20)
        body.addWidget(self.status_label)

        layout.addLayout(body)
        self._load_data()

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
            }}
            QProgressBar::chunk {{
                background-color: {c["accent"]};
                border-radius: 4px;
            }}
            QTabWidget::pane {{
                border: 1px solid {c["group_border"]};
                border-radius: 8px;
                background-color: {c["bg"]};
            }}
            QTabBar::tab {{
                padding: 8px 16px;
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
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {c['text']}; background: transparent; border: none;")
        frame_layout.addWidget(title)

        ver = QLabel(f"Installed: {addon_ver}")
        ver.setStyleSheet(f"font-size: 12px; color: {c['muted']}; background: transparent; border: none;")
        frame_layout.addWidget(ver)

        return frame

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
        desc.setStyleSheet(f"color: {c['muted']}; font-size: 11px; font-weight: normal;")
        latest_layout.addWidget(desc)

        self.latest_tag_label = QLabel("Checking...")
        self.latest_tag_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {c['muted']};")
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
        self.update_latest_btn.setEnabled(False)
        latest_layout.addWidget(self.update_latest_btn)
        layout.addWidget(latest_group)

        # Specific release
        specific_group = QGroupBox("Specific Release")
        specific_layout = QVBoxLayout(specific_group)
        specific_layout.setSpacing(8)

        pick_label = QLabel("Choose a version:")
        pick_label.setStyleSheet(f"color: {c['muted']}; font-size: 11px; font-weight: normal;")
        specific_layout.addWidget(pick_label)

        self.release_combo = QComboBox()
        self.release_combo.addItem("Loading...")
        specific_layout.addWidget(self.release_combo)

        self.release_btn = QPushButton("Install Selected Release")
        self.release_btn.setMinimumHeight(34)
        self.release_btn.clicked.connect(self._on_release_update)
        self.release_btn.setEnabled(False)
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

        warning = QLabel(
            "⚠ Do not use this if you installed Ankimon by cloning the git "
            "repository. The updater overwrites files in place and would clobber "
            "your checkout — update with 'git pull' instead. (Your Pokémon "
            "data and sprites are always preserved.)"
        )
        warning.setStyleSheet(f"color: {c['warning']}; font-size: 11px; font-weight: bold;")
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
                for b in self._branches:
                    self.target_combo.addItem(b["name"], b)
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
        def bg(_col):
            return (fetch_releases(), fetch_tags(), fetch_branches(), fetch_open_prs())

        def on_done(result):
            self._releases, self._tags, self._branches, self._prs = result
            self._populate_ui()

        QueryOp(parent=self, op=bg, success=on_done).without_collection().run_in_background()

    def _populate_ui(self):
        c = self._colors
        if self._releases:
            latest = self._releases[0]["name"]
            if latest == addon_ver:
                self.latest_tag_label.setText(f"You're up to date  ({latest})")
                self.latest_tag_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {c['success']};")
                self.update_latest_btn.setText("Already Up to Date")
            else:
                self.latest_tag_label.setText(f"New version available: {latest}")
                self.latest_tag_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {c['warning']};")
                self.update_latest_btn.setEnabled(True)
        else:
            self.latest_tag_label.setText("Could not check for updates.")
            self.latest_tag_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {c['error']};")

        self.release_combo.clear()
        if self._releases:
            for r in self._releases:
                self.release_combo.addItem(r["name"], r)
            self.release_btn.setEnabled(True)
        else:
            self.release_combo.addItem("No releases found")

        source = self.source_combo.currentData()
        if source and source != "main":
            self._populate_target(source)

    # --- Actions ---

    def _set_busy(self, busy: bool):
        self.progress_bar.setVisible(busy)
        self.progress_bar.setValue(0)
        self.update_latest_btn.setEnabled(not busy)
        self.release_btn.setEnabled(not busy)
        self.dev_install_btn.setEnabled(not busy)
        if not busy:
            self.status_label.setText("")

    def _on_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            mw.taskman.run_on_main(lambda: self.progress_bar.setValue(percent))

    def _run_update(self, download_fn, label: str, extra_warning: str = None):
        prompt = f"Update Ankimon to {label}?\n\nYour Pokemon data, settings, and sprites will be preserved."
        if extra_warning:
            prompt = f"{extra_warning}\n\n{prompt}"
        confirm = QMessageBox.question(
            self, "Confirm Update",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self.status_label.setText(f"Downloading {label}...")

        def bg(_col):
            zip_path = download_fn(progress_cb=self._on_progress)
            if not zip_path:
                return False, "Download failed. Check your internet connection.", []
            messages = []
            def status_update(m):
                messages.append(m)
                mw.taskman.run_on_main(lambda: self.status_label.setText(m))

            success, msg = apply_update(zip_path, status_cb=status_update)
            return success, msg, messages

        def on_done(result):
            self._set_busy(False)
            success, msg, messages = result
            self.status_label.setText(messages[-1] if messages else msg)
            self.progress_bar.setValue(100 if success else 0)
            if success:
                QMessageBox.information(self, "Update Complete", f"{msg}\n\nPlease restart Anki for changes to take effect.")
            else:
                QMessageBox.warning(self, "Update Failed", msg)

        QueryOp(parent=self, op=bg, success=on_done).without_collection().run_in_background()

    def _on_latest_release_update(self):
        if not self._releases:
            return
        r = self._releases[0]
        self._run_update(lambda progress_cb: _download_zip_to_temp(r["zipball_url"], progress_cb), f"latest release ({r['name']})")

    def _on_release_update(self):
        data = self.release_combo.currentData()
        if data:
            self._run_update(lambda progress_cb: _download_zip_to_temp(data["zipball_url"], progress_cb), f"release {data['name']}")

    def _on_dev_install(self):
        source = self.source_combo.currentData()
        if source == "main":
            self._run_update(lambda progress_cb: _download_branch_zip("main", progress_cb), "latest main")
        elif source == "pr":
            data = self.target_combo.currentData()
            if data:
                self._run_update(
                    lambda progress_cb: _download_pr_zip(data["head_sha"], progress_cb),
                    f"PR #{data['number']} ({data['title']})",
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
                self._run_update(lambda progress_cb: _download_branch_zip(data["name"], progress_cb), f"branch {data['name']}")
        elif source == "tag":
            data = self.target_combo.currentData()
            if data:
                self._run_update(lambda progress_cb: _download_zip_to_temp(data["zipball_url"], progress_cb), f"tag {data['name']}")
