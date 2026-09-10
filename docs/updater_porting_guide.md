# Porting Guide: Startup Update Notification System to Main/Generic Branches

This guide provides the complete architectural breakdown, implementation steps, and code diffs required to port the automatic startup update notification system from `BRRRR_Experimental` to the `main` branch (or any other branch) of Ankimon.

---

## 1. Architectural Overview

The branch update system consists of three main layers that work together asynchronously:

```mermaid
graph TD
    A[Anki Startup Hook] -->|check_branch_update| B[changelog.py]
    B -->|Read state| C[update_state.json]
    B -->|Asynchronous QueryOp| D[GitHub API]
    D -->|Compare SHAs| E{Update Available?}
    E -->|No| F[Proceed to normal load]
    E -->|Yes| G[BranchUpdatePromptDialog]
    G -->|Update Now| H[BranchUpdateProgressDialog]
    H -->|apply_update| I[Replace Addon Files]
    I -->|Save metadata| C
```

1. **State Persistence (`update_state.json`)**: Keeps track of the local installation's source type (e.g., `branch`), source name (e.g., `main`), installed commit SHA, and installation timestamp (`installed_at`).
2. **Asynchronous Check Hook (`changelog.py`)**: Runs silently on Anki startup using a non-blocking `QueryOp` to compare the local commit SHA with the remote branch's latest commit SHA from the GitHub API.
3. **Asynchronous Update Dialogs (`update_dialog.py`)**: Prompts the user with a styled commits log feed, handles a 1-week snooze, and displays a live update progress bar while downloading and extracting files under strict preservation guards.

---

## 2. Key Strategies for Main Branch Porting

To adapt this feature for the `main` branch (or any generic branch), we should implement two main design patterns:

### A. Make the Target Branch Generic
Instead of hardcoding the branch to `"BRRRR_Experimental"`, the check logic should dynamically read `source_name` from `update_state.json`. If `source_type == "branch"`, it checks for updates against **whatever branch name is stored in the state**.
* If a developer is tracking `"main"`, they get `main` updates.
* If a beta tester is tracking `"dev"`, they get `dev` updates.
* If no state file exists, it defaults to the active production branch (e.g., `"main"`).

### B. SQLite WAL & Database Protection
During extraction, standard updates completely replace the addon folder. To prevent losing user progress or database corruption (especially under SQLite Write-Ahead Logging `WAL` mode), the updater must unconditionally preserve:
1. `user_files/` directory (entirely, protecting `.db`, `.db-shm`, and `.db-wal` transaction caches).
2. `HelpInfos.html`, `updateinfos.md`, `meta.json` (root settings files).
3. `update_state.json` (updater's own status file).

`meta.json` is preserved through extraction but must then be **re-dated** — see
[Stamp the install date](#modify-stamp-the-install-date) below. Preserving it
alone is not enough: Anki decides an add-on is out of date by comparing that
file's `mod` timestamp against AnkiWeb's, so carrying the old value forward makes
Anki offer the AnkiWeb build as an "update" to code that is already newer.

---

## 3. Step-by-Step Porting Changes & Diffs

Below are the exact code modifications required across each file.

### File 1: `src/Ankimon/pyobj/update_manager.py` (Core Engine)

Add helpers to query commit dates, query branch SHAs, and manage the local JSON metadata file.

#### [NEW CODE] Commit & Branch API Methods
Add these methods to the bottom of the API requests section:

```python
def fetch_branch_sha(branch: str) -> Optional[str]:
    """Fetch the latest commit SHA of a remote branch from the GitHub API."""
    data = _api_get(f"branches/{branch}")
    if data and "commit" in data:
        return data["commit"].get("sha")
    return None

def fetch_commit_date(sha: str) -> Optional[str]:
    """Fetch the committer/author date of a specific commit SHA."""
    if not sha or len(sha) < 7 or not all(c in "0123456789abcdefABCDEF" for c in sha):
        return None
    data = _api_get(f"commits/{sha}")
    if data and isinstance(data, dict) and "commit" in data:
        committer = data["commit"].get("committer") or {}
        author = data["commit"].get("author") or {}
        return committer.get("date") or author.get("date")
    return None

def fetch_branch_commits(branch: str, local_sha: Optional[str] = None) -> list[dict]:
    """Compare local vs remote branch commits, or fetch last 5 commits as fallback."""
    try:
        if local_sha and not local_sha.startswith("old_mocked") and len(local_sha) >= 7 and all(c in "0123456789abcdefABCDEF" for c in local_sha):
            # Attempt to get the commits differential via compare API
            url = f"compare/{local_sha}...{branch}"
            data = _api_get(url)
            if data and "commits" in data:
                commits = data["commits"]
                return [{"sha": c["sha"][:7], "message": c["commit"]["message"].splitlines()[0]} for c in reversed(commits)]
        
        # Fallback: get the last 5 commits directly
        data = _api_get(f"commits?sha={branch}&per_page=5")
        if data and isinstance(data, list):
            return [{"sha": c["sha"][:7], "message": c["commit"]["message"].splitlines()[0]} for c in data]
    except Exception:
        pass
    return []
```

#### [NEW CODE] State Management Methods
Add these metadata read/write functions:

```python
def get_update_state_path() -> Path:
    return addon_dir / "user_files" / "update_state.json"

def save_update_state(source_type: str, source_name: str, commit_sha: str, skip_until: Optional[float] = None):
    """Save the metadata of the installed build to update_state.json."""
    try:
        import time
        path = get_update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "source_type": source_type,
            "source_name": source_name,
            "commit_sha": commit_sha,
            "installed_at": time.time()
        }
        if skip_until is not None:
            state["skip_until"] = skip_until
        elif path.exists():
            try:
                old_state = json.loads(path.read_text(encoding="utf-8"))
                if "skip_until" in old_state:
                    state["skip_until"] = old_state["skip_until"]
            except Exception:
                pass
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Ankimon Updater: Failed to save update state: {e}")

def set_update_skip_until(skip_until: float):
    """Snooze startup checking notifications."""
    try:
        state = read_update_state() or {}
        state["skip_until"] = skip_until
        path = get_update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Ankimon Updater: Failed to set skip_until: {e}")

def read_update_state() -> Optional[dict]:
    """Read local update state."""
    try:
        path = get_update_state_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None
```

#### [MODIFY] Database and State Preservation Guards
Update `_should_preserve` to ensure user databases, custom sprites, and the update state metadata are not deleted during update extraction:

```python
def _should_preserve(rel_path: str, gitignore_patterns: list[str]) -> bool:
    # 1. Unconditionally preserve entire user_files directory (databases, WAL caches, sprites, backups)
    if rel_path == "user_files" or rel_path.startswith("user_files/"):
        return True

    # 2. Unconditionally preserve root configs
    always_preserve_roots = ["HelpInfos.html", "updateinfos.md", "meta.json"]
    if rel_path in always_preserve_roots:
        return True

    # 3. Match user custom gitignores
    for pattern in gitignore_patterns:
        pattern = pattern.rstrip("/")
        if rel_path == pattern or rel_path.startswith(pattern + "/"):
            return True
        elif "*" in pattern:
            import fnmatch
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(os.path.basename(rel_path), pattern):
                return True

    # 4. Explicit fallback paths
    always_preserve = ["user_files/sprites/", "user_files/ankimon.db", "user_files/ankimonDEV.db", "user_files/update_state.json"]
    for p in always_preserve:
        p = p.rstrip("/")
        if rel_path == p or rel_path.startswith(p + "/"):
            return True
    return False
```

#### [MODIFY] Save State on Successful Upgrades
Modify the final section of `apply_update(...)` to write the update state upon successfully installing new files:

```diff
             # Clean up temporary folders
             try:
                 shutil.rmtree(temp_dir)
             except Exception:
                 pass
 
+            # Save local update metadata
+            save_update_state(source_type, source_name, commit_sha or "")
+
             return True, "Ankimon updated successfully!"
```

#### [MODIFY] Stamp the install date

`AddonManager.install()` is the only thing that normally writes `meta.json`'s
`mod`, and an in-app updater bypasses it entirely — it copies files in place. So
without this step `mod` keeps describing whichever build *Anki* last installed,
and Anki offers the AnkiWeb copy as an update to a newer GitHub build. Accepting
it downgrades the user. This is the whole reason `stamp_addon_mod` exists.

**Split this across two threads.** `apply_update(...)` runs in a `QueryOp`
worker, but `meta.json` is Anki's file: `writeConfig`, `toggleEnabled` and
`write_addon_meta` all read-modify-write the whole dict, and all of them run on
the main thread. A worker doing its own read-modify-write races them — its
snapshot, taken before a concurrent config write, gets replaced over the top and
silently reverts it. Writing the file atomically prevents a *torn* file; it does
nothing about a *stale* one.

So resolve the timestamp in the worker (it needs the network) and return it;
write it from the `QueryOp` success callback, which Anki guarantees runs on the
main thread.

Resolve it as the **last** thing `apply_update(...)` does, after the backup
cleanup — anything that can still raise must run before it — and return it only
on the success path, so a rolled-back install reports `None`:

```diff
             # Save local update metadata
             save_update_state(source_type, source_name, commit_sha or "")

             # ... cleanup(), final log(), backup rmtree ...

+            # Work out the date to stamp meta.json with, but do NOT write it
+            # here — see below. Guarded separately: resolving needs the network,
+            # and a GitHub hiccup must not fail an install that already
+            # succeeded.
+            pending_mod = None
+            try:
+                pending_mod = resolve_build_mtime(
+                    source_type, source_name, commit_sha, published_at
+                )
+            except Exception as e:
+                print(f"Ankimon Updater: Could not date the install: {e}")
+
-            return True, "Ankimon updated successfully!"
+            return True, "Ankimon updated successfully!", pending_mod
```

Every other `return` in the function — the git-clone guard, the archive checks
and the rollback handler — grows a third element of `None`. Then, in the dialog's
success callback:

```diff
         def on_done(result):
             try:
-                success, msg = result
+                success, msg, pending_mod = result
             except Exception as exc:
                 on_failed(exc)
                 return
+            # Main thread (QueryOp guarantees it) — the only safe place to
+            # read-modify-write meta.json. `success` is what authorises the
+            # write: dating a rolled-back build as the new one would make Anki
+            # read the old code as current and suppress the update that repairs it.
+            if success and pending_mod:
+                stamp_addon_mod(pending_mod)
```

Do this at **every** call site — it is easy to convert one and leave another
unpacking a two-tuple, and because the unpack sits inside a broad `except` the
mistake shows up only as a runtime "update failed unexpectedly", never as a
traceback. Cover each path with a test that round-trips the worker function into
the success callback.

`stamp_addon_mod` deliberately edits the single key it owns rather than going
through `AddonManager.write_addon_meta`, which re-derives six fields from an
`AddonMeta` dataclass (`disabled`, `conflicts`, `min_point_version`,
`max_point_version`, `branch_index`, `update_enabled`) and writes
non-atomically.

A release (or a tag naming one) is dated by its `published_at`; anything else by
the commit it was built from. The value never moves backwards, and the commit
path is capped at the present so a skewed clock cannot pin `mod` into the future.

---

### File 2: `src/Ankimon/changelog.py` (Startup Asynchronous Checks)

Create the check operation using a non-blocking `QueryOp` to fetch remote updates and display prompts without freezing the main thread.

#### [NEW CODE] Asynchronous Check Operation
Add the import statements and the `check_branch_update(...)` function:

```python
import time
from aqt import mw
from aqt.operations import QueryOp
from aqt.utils import showWarning

def check_branch_update(online_connectivity: bool, ssh: bool):
    """Asynchronously check for updates if tracking a branch, prompting on new commits."""
    if not online_connectivity:
        return

    from .pyobj.update_manager import read_update_state
    state = read_update_state()
    if not state:
        return  # User has not updated using the manager yet, normal startup

    source_type = state.get("source_type")
    branch_name = state.get("source_name")
    local_sha = state.get("commit_sha")
    skip_until = state.get("skip_until", 0)

    # 1. Skip checks if tracking a release/tag, or if currently snoozed
    if source_type != "branch" or not branch_name:
        return
    if isinstance(skip_until, (int, float)) and skip_until > time.time():
        return

    # 2. Background check thread
    def bg_check(_col):
        from .pyobj.update_manager import fetch_branch_sha, fetch_branch_commits
        remote_sha = fetch_branch_sha(branch_name)
        commits = []
        if remote_sha and local_sha != remote_sha:
            # New commits exist! Fetch differential to show the user
            commits = fetch_branch_commits(branch_name, local_sha)
        return remote_sha, commits

    # 3. Main UI callback upon retrieval
    def on_check_done(result):
        remote_sha, commits = result
        if not remote_sha or local_sha == remote_sha:
            return  # Up to date

        # Display the update prompt
        from .pyobj.update_dialog import BranchUpdatePromptDialog, BranchUpdateProgressDialog
        from .pyobj.update_manager import _download_branch_zip

        dialog = BranchUpdatePromptDialog(branch_name, remote_sha, commits)
        if dialog.exec():
            # User accepted: Show progress dialog and execute download/install
            prog_dialog = BranchUpdateProgressDialog(branch_name, remote_sha)
            prog_dialog.show()

            def bg_update(_col):
                messages = []
                def status_update(m):
                    messages.append(m)
                    mw.taskman.run_on_main(lambda: prog_dialog.status_label.setText(m))

                # Download branch code
                zip_path = _download_branch_zip(branch_name, progress_cb=prog_dialog._on_progress)
                if not zip_path:
                    return False, "Download failed. Check connection.", None

                # Install. Returns the date to stamp meta.json with rather than
                # writing it — this is a worker thread; see "Stamp the install date".
                from .pyobj.update_manager import apply_update
                return apply_update(zip_path, "branch", branch_name, remote_sha, status_cb=status_update)

            def on_update_done(update_result):
                prog_dialog.close()
                success, msg, pending_mod = update_result
                # Main thread: the only safe place to read-modify-write meta.json.
                if success and pending_mod:
                    from .pyobj.update_manager import stamp_addon_mod
                    stamp_addon_mod(pending_mod)
                if success:
                    from aqt.utils import showInfo
                    showInfo("Update complete! Please restart Anki for changes to take effect.")
                else:
                    showWarning(f"Update failed: {msg}")

            QueryOp(parent=mw, op=bg_update, success=on_update_done).without_collection().run_in_background()

    QueryOp(parent=mw, op=bg_check, success=on_check_done).without_collection().run_in_background()
```

---

### File 3: `src/Ankimon/__init__.py` (Startup Hooks Registration)

Hook the asynchronous check function into Anki's loading lifecycle.

#### [MODIFY] Registration sequence
Import `check_branch_update` and register it inside the startup sequence:

```diff
 # --- Changelog check ---
 try:
     from .changelog import check_and_show_changelog, check_branch_update
     
     # Runs normal release changelog
     check_and_show_changelog()
     
+    # Runs asynchronous branch delta checking
+    check_branch_update(online_connectivity=True, ssh=False)
 except Exception as e:
     pass
```

> **(Note: on the integration branch this is routed through the services seam.)** The
> self-updater (F26) has since landed on the integration branch, but it is *not* wired as
> the raw inline `check_branch_update(online_connectivity=True, ssh=False)` call shown
> above. `__init__.py` captures connectivity once at boot (`test_online_connectivity()`)
> and calls `schedule_branch_update_check(online_connectivity, ssh)`, which defers the
> poll to the profile-open (`gui_hooks`) seam — mirroring how the base gates
> `check_and_show_changelog()` — rather than performing a module-level side effect.

---

### File 4: `src/Ankimon/pyobj/update_dialog.py` (UI Widgets)

Implement user-friendly prompts with complete style and night-mode integration.

#### [NEW CODE] Prompt and Progress Dialog Classes
Add the complete UI classes:

{% raw %}
```python
class BranchUpdatePromptDialog(QDialog):
    """Adaptive, dark-mode aware popup displaying commit logs & snooze checkbox."""
    def __init__(self, branch_name: str, remote_sha: str, commits: list[dict] = None, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Ankimon Update Available")
        self.setMinimumWidth(460)
        self.resize(520, 420) if commits else self.resize(480, 240)

        # Apply adaptive theme palettes
        is_dark = theme_manager.night_mode
        bg = "#2b2b2b" if is_dark else "#ffffff"
        text = "#e0e0e0" if is_dark else "#212121"
        border = "#444444" if is_dark else "#e0e0e0"
        btn_bg = "#3d3d3d" if is_dark else "#eeeeee"
        btn_hover = "#505050" if is_dark else "#e0e0e0"
        btn_primary = "#1976d2"
        
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QLabel {{ color: {text}; font-size: 13px; }}
            QPushButton {{
                padding: 8px 16px; border: 1px solid {border}; border-radius: 6px;
                background-color: {btn_bg}; color: {text}; font-size: 13px; min-width: 100px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
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

        # Styled QTextBrowser scrollable commit feed
        if commits:
            commits_box = QTextBrowser()
            commits_box.setReadOnly(True)
            commits_box.setOpenExternalLinks(True)
            
            box_bg = "#333333" if is_dark else "#fafafa"
            box_border = "#444444" if is_dark else "#e0e0e0"
            accent_color = "#4fc3f7" if is_dark else "#1976d2"
            
            commits_box.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: {box_bg}; border: 1px solid {box_border};
                    border-radius: 6px; padding: 8px; font-size: 12px; color: {text};
                }}
            """)
            
            import html
            html_content = "<b>What's New on Branch:</b><br><ul style='margin-top: 4px; margin-bottom: 4px; padding-left: 20px;'>"
            for c in commits:
                sha = c.get("sha", "")
                msg = c.get("message", "")
                html_content += f"<li style='margin-bottom: 4px;'><code><font color='{accent_color}'>{sha}</font></code> - {html.escape(msg)}</li>"
            html_content += "</ul>"
            
            commits_box.setHtml(html_content)
            layout.addWidget(commits_box)

        prompt_label = QLabel("Would you like to install the latest changes now?")
        layout.addWidget(prompt_label)

        self.skip_checkbox = QCheckBox("Don't notify me for 1 week")
        self.skip_checkbox.setStyleSheet(f"color: {text}; font-size: 12px; margin-top: 4px;")
        layout.addWidget(self.skip_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_later = QPushButton("Later")
        self.btn_later.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_later)

        self.btn_update = QPushButton("Update Now")
        self.btn_update.setStyleSheet(f"QPushButton {{ background-color: {btn_primary}; color: white; border: none; font-weight: bold; }} QPushButton:hover {{ background-color: #1565c0; }}")
        self.btn_update.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_update)

        layout.addLayout(btn_layout)

    def reject(self):
        if self.skip_checkbox.isChecked():
            import time
            from .update_manager import set_update_skip_until
            one_week_later = time.time() + 604800
            set_update_skip_until(one_week_later)
        super().reject()

class BranchUpdateProgressDialog(QDialog):
    """Dialog displaying a download progress bar during startup updates."""
    def __init__(self, branch_name: str, remote_sha: str, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Updating Ankimon")
        self.setMinimumWidth(440)
        self.resize(480, 200)

        is_dark = theme_manager.night_mode
        bg = "#2b2b2b" if is_dark else "#ffffff"
        text = "#e0e0e0" if is_dark else "#212121"
        border = "#444444" if is_dark else "#e0e0e0"
        accent = "#4fc3f7" if is_dark else "#1976d2"

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QProgressBar {{
                border: none; background-color: {border}; border-radius: 4px;
                text-align: center; height: 16px; color: {text};
            }}
            QProgressBar::chunk {{ background-color: {accent}; border-radius: 4px; }}
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
        layout.addWidget(self.progress_bar)

    def _on_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            mw.taskman.run_on_main(lambda: self.progress_bar.setValue(percent))
```
{% endraw %}

---

## 4. Key Takeaways for the Main Branch Release

Porting this setup into the `main` branch provides immediate, production-ready benefits:

1. **Perfect Startup Flow**: Because checks run asynchronously in background queries, users never experience UI lag or freezing during startups, even under high packet loss or offline conditions.
2. **Absolute Data Security**: By isolating all database types (`.db`, `.db-wal`, `.db-shm`) and media sprites into unconditional preservation filters inside `_should_preserve`, we ensure updates can be run repeatedly without any risk of user data loss.
3. **Weekly Snoozing UX**: Prevents pop-up fatigue, giving power users complete control over update intervals.
