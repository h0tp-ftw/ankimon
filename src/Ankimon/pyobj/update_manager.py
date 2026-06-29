import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Optional

from aqt import mw
from aqt.operations import QueryOp

from ..resources import addon_dir

REPO_OWNER = "h0tp-ftw"
REPO_NAME = "ankimon"
GITHUB_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
DOWNLOAD_TIMEOUT = 30
USER_AGENT = "Ankimon-Updater (https://github.com/h0tp-ftw/ankimon)"
DEFAULT_SUBMODULE_SHA = "f3092b03fbe1e37d1788ef802dee98906d621e36"
# The in-app updater shipped in v2.0. Installing an older release would strip the
# updater out (older versions predate it), so pre-2.0 versions are filtered from
# the release/tag pickers — going back would break the update feature itself.
MIN_UPDATER_VERSION = (2, 0)


def _git_repo_root() -> Optional[Path]:
    """Return the git repo root that contains the addon, else None.

    For a GitHub clone the addon is ``src/Ankimon`` nested in the repo, so the
    ``.git`` directory sits two levels *above* ``addon_dir`` at the repo root —
    hence the upward parent walk. ``resolve()`` first follows the dev symlink
    from ``addons21/`` into the repo so those parents land on the real repo root;
    the walk is kept shallow to avoid false positives from an unrelated repo
    higher up.
    """
    try:
        base = Path(addon_dir).resolve()
    except Exception:
        base = Path(addon_dir)
    for d in [base, *list(base.parents)[:3]]:
        # .exists() rather than .is_dir(): in git worktrees and some submodule
        # layouts ".git" is a *file* (containing "gitdir: ..."), not a directory.
        if (d / ".git").exists():
            return d
    return None


def is_git_clone() -> bool:
    """True if Ankimon runs from a git working tree (a dev clone).

    The in-place updater would overwrite every file under ``addon_dir`` with the
    downloaded copy, trashing the working tree and any uncommitted/untracked
    changes (``.git`` is above ``addon_dir`` so it survives, but the checkout is
    clobbered). So the destructive updater is hidden for clones; a safe
    ``git pull --ff-only`` is offered instead (see ``git_pull_ff_only``).
    """
    return _git_repo_root() is not None


def git_pull_ff_only(status_cb=None) -> tuple[bool, str]:
    """Update a dev clone via ``git pull --ff-only`` (+ submodule update).

    Safe by construction: ``--ff-only`` refuses, without changing anything, when
    the tree is dirty or the branch has diverged — so it never creates merge
    conflicts. Returns ``(success, message)``.
    """
    def log(msg):
        if status_cb:
            status_cb(msg)

    root = _git_repo_root()
    if root is None:
        return False, "Ankimon is not running from a git checkout."

    if shutil.which("git") is None:
        return False, (
            "git wasn't found on Anki's PATH. This is common when Anki is "
            "launched from the dock/Finder, which doesn't inherit your shell "
            "PATH. Restart Anki from a terminal, or run 'git pull' in your "
            "clone yourself."
        )

    def _git(*args, timeout=180):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        branch = (_git("rev-parse", "--abbrev-ref", "HEAD", timeout=30).stdout or "").strip() or "HEAD"
        log(f"Fast-forwarding '{branch}' (git pull --ff-only)...")
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout or "").strip()
            return False, (
                f"Could not fast-forward '{branch}'. This is expected if you "
                "have local changes/commits or no upstream — resolve it manually "
                "with git.\n\n" + err
            )
        log("Updating submodules...")
        sub = _git("submodule", "update", "--init", "--recursive")
        out = (pull.stdout or "").strip()
        if sub.returncode != 0:
            return True, (
                f"Updated '{branch}', but the submodule update failed — run "
                "'git submodule update --init --recursive' manually.\n\n"
                + (sub.stderr or "").strip()
            )
        return True, f"Updated '{branch}' via git pull --ff-only.\n\n{out}"
    except subprocess.TimeoutExpired:
        return False, "git timed out. Update manually with 'git pull'."
    except Exception as e:
        return False, f"git update failed: {e}. Update manually with 'git pull'."


def _parse_version(name: str) -> Optional[tuple]:
    """Coarse (major, minor) parse for the MIN_UPDATER_VERSION threshold gate ONLY.

    Returns None for non-version names ('sprites', 'nightly-release', 'archive/*').

    This is deliberately NOT a total ordering. It stops at minor and reads each
    component as an int, so '2.01' and '2.1' both parse to (2, 1) and a '2.0.1'
    patch is dropped. That is harmless for the `>= (2, 0)` threshold but WRONG for
    sorting or equality: do not pass it to sorted()/max() or use it to compare two
    releases. "Latest" is resolved by GitHub API order (newest first, see
    fetch_releases / update_dialog), never by this function.
    """
    m = re.match(r"v?(\d+)(?:\.(\d+))?", name.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0))


def _is_supported_version(name: str) -> bool:
    """True for parseable version names >= MIN_UPDATER_VERSION. Non-version names
    (the 'sprites' asset release, 'nightly-release', 'archive/*', …) return False
    and are excluded from the pickers."""
    v = _parse_version(name)
    return v is not None and v >= MIN_UPDATER_VERSION


def _make_request(url: str, accept: str = "application/vnd.github.v3+json") -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", USER_AGENT)
    return req


def _api_get(endpoint: str) -> Optional[dict]:
    url = f"{GITHUB_API}/{endpoint}"
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _fetch_gitignore_patterns() -> list[str]:
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/.gitignore"
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:
            lines = resp.read().decode().splitlines()
        patterns = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cleaned = line.replace("src/Ankimon/", "").strip("/")
            if cleaned:
                patterns.append(cleaned)
        return patterns
    except Exception:
        return []


def _should_preserve(rel_path: str, gitignore_patterns: list[str]) -> bool:
    # Holy Ground: NEVER touch anything in user_files/ during an update,
    # regardless of gitignore patterns.
    if rel_path == "user_files" or rel_path.startswith("user_files/"):
        return True

    for pattern in gitignore_patterns:
        pattern = pattern.rstrip("/")
        if rel_path == pattern or rel_path.startswith(pattern + "/"):
            return True
        elif "*" in pattern:
            import fnmatch
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(os.path.basename(rel_path), pattern):
                return True

    always_preserve = ["user_files/sprites/", "user_files/ankimon.db"]
    for p in always_preserve:
        p = p.rstrip("/")
        if rel_path == p or rel_path.startswith(p + "/"):
            return True
    return False


def fetch_tags() -> list[dict]:
    data = _api_get("tags")
    if not data:
        return []
    return [
        {"name": t["name"], "zipball_url": t["zipball_url"]}
        for t in data
        if _is_supported_version(t["name"])
    ]


def fetch_releases() -> list[dict]:
    data = _api_get("releases")
    if not data:
        return []
    return [
        {"name": r["tag_name"], "body": r.get("body", ""), "zipball_url": r["zipball_url"]}
        for r in data
        if _is_supported_version(r["tag_name"])
    ]


def fetch_branches() -> list[dict]:
    data = _api_get("branches")
    if not data:
        return []
    return [{"name": b["name"]} for b in data]


def fetch_open_prs() -> list[dict]:
    data = _api_get("pulls?state=open&per_page=50")
    if not data:
        return []
    return [{"number": pr["number"], "title": pr["title"], "head_ref": pr["head"]["ref"], "head_sha": pr["head"]["sha"]} for pr in data]


def _download_zip_to_temp(url: str, progress_cb=None) -> Optional[str]:
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            
            # Create a named temporary file that persists after closing the object
            # but is cleaned up by our manual logic later.
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            tmp_path = tmp.name
            
            try:
                downloaded = 0
                chunk_size = 128 * 1024  # 128KB chunks
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    tmp.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded, total)
                tmp.close()
                return tmp_path
            except Exception:
                tmp.close()
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
    except Exception:
        return None


def _download_branch_zip(branch: str, progress_cb=None) -> Optional[str]:
    url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{branch}.zip"
    return _download_zip_to_temp(url, progress_cb)


def _download_pr_zip(head_sha: str, progress_cb=None) -> Optional[str]:
    url = f"{GITHUB_API}/zipball/{head_sha}"
    return _download_zip_to_temp(url, progress_cb)


def _get_gitignore_patterns() -> list[str]:
    patterns = _fetch_gitignore_patterns()
    if not patterns:
        patterns = [
            "user_files/mypokemon.json", "user_files/mainpokemon.json",
            "user_files/badges.json", "user_files/items.json",
            "user_files/data.json", "user_files/team.json",
            "user_files/config.obf", "user_files/pokemon_history.json",
            "user_files/rate_this.json", "user_files/backups",
            "user_files/todays_shop.json", "user_files/meta.json",
            "user_files/download_complete.flag", "user_files/ankimon.db",
            "user_files/json/*", "user_files/sprites/",
            "meta.json", "*.pyc", "*.log",
        ]
    return patterns


def _find_src_prefix(names: list[str]) -> Optional[str]:
    for name in names:
        if name.endswith("src/Ankimon/"):
            return name
    for name in names:
        if "src/Ankimon/__init__.py" in name:
            return name.rsplit("src/Ankimon/__init__.py", 1)[0] + "src/Ankimon/"
    return None


def _collect_code_files(gitignore_patterns: list[str]) -> dict[str, Path]:
    code_files = {}
    for root, dirs, files in os.walk(addon_dir):
        for fname in files:
            full_path = Path(root) / fname
            rel = str(full_path.relative_to(addon_dir)).replace("\\", "/")
            if not _should_preserve(rel, gitignore_patterns):
                code_files[rel] = full_path
    return code_files


def _extract_ref_from_prefix(src_prefix: str) -> str:
    # src_prefix is e.g. "ankimon-main/src/Ankimon/" or "h0tp-ftw-ankimon-a1b2c3d/src/Ankimon/"
    parts = src_prefix.strip("/").replace("\\", "/").split("/")
    if not parts:
        return "main"
    root_dir = parts[0]
    # Remove repo name prefix if present
    if root_dir.startswith("ankimon-"):
        ref = root_dir[len("ankimon-"):]
        return ref if ref else "main"
    elif "ankimon-" in root_dir:
        # e.g. h0tp-ftw-ankimon-a1b2c3d -> a1b2c3d
        ref = root_dir.split("ankimon-")[-1]
        return ref if ref else "main"
    return "main"


def _fetch_submodule_sha(ref: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/src/Ankimon/poke_engine?ref={ref}"
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, dict) and data.get("type") == "submodule":
                return data.get("sha")
    except Exception:
        pass
    return None


def _download_and_extract_submodule(sha: str, dest_dir: Path, status_cb=None):
    def log(msg):
        if status_cb:
            status_cb(msg)

    url = f"https://github.com/ArdentRoe/poke-engine/archive/{sha}.zip"
    log("Downloading poke_engine submodule package...")
    
    zip_path = _download_zip_to_temp(url)
    if not zip_path:
        raise Exception("Failed to download poke_engine submodule zip archive.")

    log("Extracting poke_engine submodule...")
    temp_extract_dir = Path(tempfile.mkdtemp(prefix="ankimon_submodule_extract_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                raise Exception("poke_engine submodule archive is empty.")
            # The root directory in zip is e.g. "poke-engine-{sha}"
            root_prefix = names[0].split("/")[0] + "/"
            
            for name in names:
                if not name.startswith(root_prefix) or name == root_prefix:
                    continue
                rel_path = name[len(root_prefix):]
                if not rel_path or rel_path.endswith("/"):
                    continue
                
                dest_file = temp_extract_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as source, dest_file.open("wb") as target:
                    shutil.copyfileobj(source, target)
        
        # Atomic swap: Delete old dest_dir if it exists, rename temp_extract_dir to dest_dir
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(temp_extract_dir), str(dest_dir))
    except Exception as e:
        if temp_extract_dir.exists():
            try:
                shutil.rmtree(temp_extract_dir)
            except Exception:
                pass
        raise e
    finally:
        if os.path.exists(zip_path):
            try:
                os.unlink(zip_path)
            except Exception:
                pass


def apply_update(zip_path: str, status_cb=None) -> tuple[bool, str]:
    def log(msg):
        if status_cb:
            status_cb(msg)

    def cleanup():
        if os.path.exists(zip_path):
            try:
                os.unlink(zip_path)
            except Exception:
                pass

    # Safety guard: this overwrites every file under addon_dir (the addon =
    # src/Ankimon) with the downloaded copy. On a git clone that trashes the
    # working tree and any uncommitted/untracked changes, so refuse. (.git lives
    # above addon_dir and is untouched, but the checkout is still clobbered.)
    if is_git_clone():
        cleanup()
        return False, (
            "Detected a git checkout of Ankimon. The in-app updater overwrites "
            "the addon's files in place and would clobber your working tree "
            "(you'd lose uncommitted changes). Update your clone with 'git pull' "
            "instead."
        )

    log("Fetching latest .gitignore from main...")
    gitignore_patterns = _get_gitignore_patterns()

    log("Validating update archive...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                return False, "ZIP archive is empty."

            src_prefix = _find_src_prefix(names)
            if not src_prefix:
                return False, "Could not find src/Ankimon/ in the archive."

            new_files = {}
            for name in names:
                if not name.startswith(src_prefix) or name == src_prefix:
                    continue
                rel_path = name[len(src_prefix):]
                if not rel_path or rel_path.endswith("/"):
                    continue
                if _should_preserve(rel_path, gitignore_patterns):
                    continue
                new_files[rel_path] = name

            if not new_files:
                return False, "No addon files found in the archive."

            log(f"Archive validated: {len(new_files)} files to install.")

            # --- Backup current code files ---
            log("Backing up current addon code...")
            backup_dir = Path(tempfile.mkdtemp(prefix="ankimon_update_backup_"))
            code_files = _collect_code_files(gitignore_patterns)
            backed_up = 0
            for rel, full_path in code_files.items():
                dest = backup_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(full_path, dest)
                    backed_up += 1
                except Exception:
                    pass
            log(f"Backed up {backed_up} code files to {backup_dir.name}.")

            # --- Apply update ---
            log("Removing old addon code...")
            for rel, full_path in code_files.items():
                try:
                    full_path.unlink()
                except Exception:
                    pass

            for root, dirs, _ in os.walk(addon_dir, topdown=False):
                for dname in dirs:
                    dir_path = Path(root) / dname
                    try:
                        if not any(dir_path.iterdir()):
                            dir_path.rmdir()
                    except Exception:
                        pass

            log("Installing new files...")
            installed = 0
            for rel_path, zip_name in new_files.items():
                if ".." in rel_path or os.path.isabs(rel_path):
                    continue
                dest = addon_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(zip_name) as source, dest.open("wb") as target:
                    shutil.copyfileobj(source, target)
                installed += 1

            # --- Download and install matching poke_engine submodule version ---
            ref = _extract_ref_from_prefix(src_prefix)
            log(f"Resolving poke_engine submodule for ref '{ref}'...")
            sub_sha = _fetch_submodule_sha(ref) or DEFAULT_SUBMODULE_SHA
            
            _download_and_extract_submodule(sub_sha, addon_dir / "poke_engine", status_cb)

            cleanup()
            log(f"Update complete. Installed {installed} files.")

            # Cleanup backup on success
            try:
                shutil.rmtree(backup_dir)
            except Exception:
                pass

            return True, "Update applied successfully. Please restart Anki."

    except Exception as e:
        # --- Rollback ---
        log(f"Update failed: {e}. Rolling back...")
        rollback_count = 0
        for root, dirs, files in os.walk(backup_dir):
            for fname in files:
                backup_path = Path(root) / fname
                rel = str(backup_path.relative_to(backup_dir)).replace("\\", "/")
                dest = addon_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(backup_path, dest)
                    rollback_count += 1
                except Exception:
                    pass
        log(f"Rolled back {rollback_count} files.")

        try:
            shutil.rmtree(backup_dir)
        except Exception:
            pass

        cleanup()
        return False, f"Update failed and was rolled back: {e}"
